#!python3

import sys, os, time, logging
from threading import Thread

package_dir = os.path.dirname(os.path.dirname(__file__))
global_log_dir = os.path.join(package_dir, 'Monitoring', 'log')

from pyhamilton import (HamiltonInterface, LayoutManager, ResourceType, Plate24, Plate96, Tip96,
    INITIALIZE, PICKUP, EJECT, ASPIRATE, DISPENSE, ISWAP_GET, ISWAP_PLACE, HEPA,
    WASH96_EMPTY, PICKUP96, EJECT96, ASPIRATE96, DISPENSE96,
    oemerr, PositionError)
from platereader.clariostar import ClarioStar, PlateData
from auxpump.pace import OffDeckCulturePumps, LBPumps
from auxshaker.bigbear import Shaker

def resource_list_with_prefix(layout_manager, prefix, res_class, num_ress):
    def name_from_line(line):
        field = LayoutManager.layline_objid(line)
        if field:
            return field
        return LayoutManager.layline_first_field(line)
    layline_test = lambda line: LayoutManager.field_starts_with(name_from_line(line), prefix)
    res_type = ResourceType(res_class, layline_test, name_from_line)
    res_list = [layout_manager.assign_unused_resource(res_type) for _ in range(num_ress)]
    res_list.sort(key=lambda r: r.layout_name())
    return res_list

def labware_pos_str(labware, idx):
    return labware.layout_name() + ', ' + labware.position_id(idx)

def compound_pos_str(pos_tuples):
    present_pos_tups = [pt for pt in pos_tuples if pt is not None]
    return ';'.join((labware_pos_str(labware, idx) for labware, idx in present_pos_tups))

def compound_pos_str_96(labware96):
    return ';'.join((labware_pos_str(labware96, idx) for idx in range(96)))

def initialize(ham, async=False):
    cmd = ham.send_command(INITIALIZE)
    if not async:
        ham.wait_on_response(cmd, raise_first_exception=True)
    return cmd

def hepa_on(ham, speed=15, async=False, **more_options):
    cmd = ham.send_command(HEPA, fanSpeed=speed, **more_options)
    if not async:
        ham.wait_on_response(cmd, raise_first_exception=True)
    return cmd

def wash_empty_refill(ham, async=False, **more_options):
    cmd = ham.send_command(WASH96_EMPTY, **more_options)
    if not async:
        ham.wait_on_response(cmd, raise_first_exception=True)
    return cmd

def move_plate(ham, source_plate, target_plate, try_inversions=None):
    logging.info('move_plate: Moving plate ' + source_plate.layout_name() + ' to ' + target_plate.layout_name())
    src_pos = labware_pos_str(source_plate, 0)
    trgt_pos = labware_pos_str(target_plate, 0)
    if try_inversions is None:
        try_inversions = (0, 1)
    for inv in try_inversions:
        cid = ham.send_command(ISWAP_GET, plateLabwarePositions=src_pos, gripHeight=6, inverseGrip=inv)
        try:
            ham.wait_on_response(cid, raise_first_exception=True, timeout=120)
            break
        except PositionError:
            pass
    else:
        raise IOError
    cid = ham.send_command(ISWAP_PLACE, plateLabwarePositions=trgt_pos)
    try:
        ham.wait_on_response(cid, raise_first_exception=True, timeout=120)
    except PositionError:
        raise IOError

def lagoon_pos_for_lagoon(lagoon_idx):
    return lagoon_plate, lagoon_idx

def clean_tip_pos_for_lagoon(lagoon_idx):
    return clean_tip_box, lagoon_idx

def dirty_tip_pos_for_lagoon(lagoon_idx):
    return dirty_tip_boxes[lagoon_idx//96], lagoon_idx%96

def offset_equal_spaced_idxs(start_idx, increment):
    # a generator that will be used for reader positions
    idx = start_idx
    while True:
        yield idx
        idx += increment

def read_plate(ham_int, reader_int, reader_site, plate, protocol_names, plate_id=None, async_task=None):
    logging.info('read_plate: Running plate protocols ' + ', '.join(protocol_names) +
            ' on plate ' + plate.layout_name() + ('' if plate_id is None else ' with id ' + plate_id))
    reader_int.plate_out(block=False)
    move_plate(ham_int, plate, reader_site)
    if async_task:
        t = run_async(async_task)
    plate_datas = reader_int.run_protocols(protocol_names, plate_id_1=plate_id)
    reader_int.plate_out(block=False)
    if async_task:
        t.join()
    move_plate(ham_int, reader_site, plate)
    return plate_datas

def channel_var(pos_tuples):
    ch_var = ['0']*16
    for i, pos_tup in enumerate(pos_tuples):
        if pos_tup is not None:
            ch_var[i] = '1'
    return ''.join(ch_var)

def tip_pick_up(ham_int, pos_tuples, **more_options):
    logging.info('tip_pick_up: Pick up tips at ' + '; '.join((labware_pos_str(*pt) if pt else '(skip)' for pt in pos_tuples)) +
            ('' if not more_options else ' with extra options ' + str(more_options)))
    num_channels = len(pos_tuples)
    if num_channels > 8:
        raise ValueError('Can only pick up 8 tips at a time')
    ch_patt = channel_var(pos_tuples)
    labware_poss = compound_pos_str(pos_tuples)
    ham_int.wait_on_response(ham_int.send_command(PICKUP,
        labwarePositions=labware_poss,
        channelVariable=ch_patt,
        **more_options), raise_first_exception=True)

def tip_eject(ham_int, pos_tuples, **more_options):
    logging.info('tip_eject: Eject tips to ' + '; '.join((labware_pos_str(*pt) if pt else '(skip)' for pt in pos_tuples)) +
            ('' if not more_options else ' with extra options ' + str(more_options)))
    num_channels = len(pos_tuples)
    if num_channels > 8:
        raise ValueError('Can only pick up 8 tips at a time')
    ch_patt = channel_var(pos_tuples)
    labware_poss = compound_pos_str(pos_tuples)
    ham_int.wait_on_response(ham_int.send_command(EJECT,
        labwarePositions=labware_poss,
        channelVariable=ch_patt,
        **more_options), raise_first_exception=True)

default_liq_class = 'HighVolumeFilter_Water_DispenseJet_Empty_with_transport_vol'

def assert_parallel_nones(list1, list2):
    if not (len(list1) == len(list2) and all([(i1 is None) == (i2 is None) for i1, i2 in zip(list1, list2)])):
        raise ValueError('Lists must have parallel None entries')

def aspirate(ham_int, pos_tuples, vols, **more_options):
    assert_parallel_nones(pos_tuples, vols)
    logging.info('aspirate: Aspirate volumes ' + str(vols) + ' from positions [' +
            '; '.join((labware_pos_str(*pt) if pt else '(skip)' for pt in pos_tuples)) +
            (']' if not more_options else '] with extra options ' + str(more_options)))
    if len(pos_tuples) > 8:
        raise ValueError('Can only aspirate with 8 channels at a time')
    ham_int.wait_on_response(ham_int.send_command(ASPIRATE,
        channelVariable=channel_var(pos_tuples),
        labwarePositions=compound_pos_str(pos_tuples),
        volumes=[v for v in vols if v is not None],
        liquidClass=default_liq_class,
        **more_options), raise_first_exception=True)

def dispense(ham_int, pos_tuples, vols, **more_options):
    assert_parallel_nones(pos_tuples, vols)
    logging.info('dispense: Dispense volumes ' + str(vols) + ' into positions [' +
            '; '.join((labware_pos_str(*pt) if pt else '(skip)' for pt in pos_tuples)) +
            (']' if not more_options else '] with extra options ' + str(more_options)))
    if len(pos_tuples) > 8:
        raise ValueError('Can only aspirate with 8 channels at a time')
    ham_int.wait_on_response(ham_int.send_command(DISPENSE,
        channelVariable=channel_var(pos_tuples),
        labwarePositions=compound_pos_str(pos_tuples),
        volumes=[v for v in vols if v is not None],
        liquidClass=default_liq_class,
        **more_options), raise_first_exception=True)

def tip_pick_up_96(ham_int, tip96, **more_options):
    logging.info('tip_pick_up_96: Pick up tips at ' + tip96.layout_name() +
            ('' if not more_options else ' with extra options ' + str(more_options)))
    labware_poss = compound_pos_str_96(tip96)
    ham_int.wait_on_response(ham_int.send_command(PICKUP96,
        labwarePositions=labware_poss,
        **more_options), raise_first_exception=True)

def tip_eject_96(ham_int, tip96, **more_options):
    logging.info('tip_eject_96: Eject tips to ' + tip96.layout_name() +
            ('' if not more_options else ' with extra options ' + str(more_options)))
    labware_poss = compound_pos_str_96(tip96)
    print('EJECTING TIPS TO' + tip96.layout_name())
    ham_int.wait_on_response(ham_int.send_command(EJECT96,
        labwarePositions=labware_poss,
        **more_options), raise_first_exception=True)
    print('DONE EJECTING TIPS TO' + tip96.layout_name())

def aspirate_96(ham_int, plate96, vol, **more_options):
    logging.info('aspirate_96: Aspirate volume ' + str(vol) + ' from ' + plate96.layout_name() +
            ('' if not more_options else ' with extra options ' + str(more_options)))
    ham_int.wait_on_response(ham_int.send_command(ASPIRATE96,
        labwarePositions=compound_pos_str_96(plate96),
        aspirateVolume=vol,
        liquidClass=default_liq_class,
        **more_options), raise_first_exception=True)

def dispense_96(ham_int, plate96, vol, **more_options):
    logging.info('dispense_96: Dispense volume ' + str(vol) + ' into ' + plate96.layout_name() +
            ('' if not more_options else ' with extra options ' + str(more_options)))
    ham_int.wait_on_response(ham_int.send_command(DISPENSE96,
        labwarePositions=compound_pos_str_96(plate96),
        dispenseVolume=vol,
        liquidClass=default_liq_class,
        **more_options), raise_first_exception=True)

def add_robot_level_log(logger_name=None):
    logger = logging.getLogger(logger_name) # root logger if None
    logger.setLevel(logging.DEBUG)
    with open(os.path.join(user_dir, '.roboid')) as roboid_f:
        robot_id = roboid_f.read()
    robot_log_dir = os.path.join(global_log_dir, robot_id, robot_id + '.log')
    hdlr = logging.FileHandler(robot_log_dir)
    formatter = logging.Formatter('[%(asctime)s] %(name)s %(levelname)s %(message)s')
    hdlr.setFormatter(formatter)
    logger.addHandler(hdlr)

def run_async(funcs):
    def go():
        try:
            iter(funcs)
        except TypeError:
            funcs()
            return
        for func in funcs:
            func()
    func_thread = Thread(target=go, daemon=True)
    func_thread.start()
    return func_thread

def yield_in_chunks(sliceable, n):
    sliceable = list(sliceable)
    start_pos = 0
    end_pos = n
    while start_pos < len(sliceable):
        yield sliceable[start_pos:end_pos]
        start_pos, end_pos = end_pos, end_pos + n

def log_banner(banner_text):
    l = len(banner_text)
    margin = 5
    width = l + 2*margin + 2
    return ['#'*width,
            '#' + ' '*(width - 2) + '#',
            '#' + ' '*margin + banner_text + ' '*margin + '#',
            '#' + ' '*(width - 2) + '#',
            '#'*width]
