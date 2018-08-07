#!python3

import sys, os, time, logging, sqlite3, types

this_file_dir = os.path.dirname(__file__)
package_dir = os.path.abspath(os.path.join(this_file_dir, '..'))
reader_results_dir = os.path.abspath(os.path.join(package_dir, 'plate_reader_results'))

basic_pace_mod_path = os.path.join(package_dir, 'basic_pace')
if basic_pace_mod_path not in sys.path:
    sys.path.append(basic_pace_mod_path)

from basic_pace_180705x import (
    LayoutManager, ResourceType, Plate24, Plate96, Tip96,
    HamiltonInterface, ClarioStar, LBPumps, Shaker, PlateData,
    initialize, hepa_on, tip_pick_up, tip_eject, aspirate, dispense, wash_empty_refill,
    tip_pick_up_96, tip_eject_96, aspirate_96, dispense_96,
    resource_list_with_prefix, read_plate,
    run_async, yield_in_chunks, log_banner)

def ensure_meas_table_exists(db_conn):
    '''
    Definitions of the fields in this table:
    Exactly one of the following should have a value:
        lagoon_number - the number of the lagoon, uniquely identifying the experiment, zero-indexed
        turb_number - the number of a turbidostat, uniquely identifying the culture contained
    filename - absolute path to the file in which this data is housed
    plate_id - ID field given when measurement was requested, should match ID in data file
    timestamp - time at which the measurement was taken
    well - the location in the plate reader plate where this sample was read, e.g. 'B2'
    measurement_delay_time - the time, in minutes, after the sample was pipetted that the
                            measurement was taken. For migration, we consider this to be 0
                            minutes in the absense of pipetting time values
    reading - the raw measured value from the plate reader
    data_type - 'lum' 'abs' or the spectra values for the fluorescence measurement
    '''
    c = db_conn.cursor()
    c.execute('''CREATE TABLE if not exists measurements
                (lagoon_number, turb_number, filename, plate_id, timestamp, well, measurement_delay_time, reading, data_type)''')
    db_conn.commit()

def db_add_plate_data(plate_data, data_type, plate, vessel_numbers, read_wells, vessel_type):
    db_conn = sqlite3.connect(os.path.join(this_file_dir, __file__.split('.')[0] + '.db'))
    ensure_meas_table_exists(db_conn)
    c = db_conn.cursor()
    for vessel_number, read_well in zip(vessel_numbers, read_wells):
        filename = plate_data.path
        plate_id = plate_data.header.plate_ids[0]
        timestamp = plate_data.header.time
        well = plate.position_id(read_well)
        measurement_delay_time = 0.0
        reading = plate_data.value_at(*plate.well_coords(read_well))
        if vessel_type == 'turbidostat':
            turb_number = vessel_number
            lagoon_number = None
        elif vessel_type == 'lagoon':
            turb_number = None
            lagoon_number = vessel_number
        else:
            raise ValueError("Only vessel types are 'turbidostat' and 'lagoon'")
        data = (lagoon_number, turb_number, filename, plate_id, timestamp, well, measurement_delay_time, 
                 reading, data_type)
        c.execute("INSERT INTO measurements VALUES (?,?,?,?,?,?,?,?,?)", data)
    db_conn.commit()
    db_conn.close()

def flow_rate_controller(od, target_od=.45, margin=.05):
    max_od = .7
    min_od = .2
    max_flow_through = 3.8
    decrease_rate = 3.8
    increase_rate = 1.5
    if od > max_od:
        return max_flow_through
    elif od > target_od + margin:
        return decrease_rate
    elif od < min_od:
        return 0
    elif od < target_od - margin:
        return increase_rate
    else:
        return (increase_rate + decrease_rate)/2

def abs_to_od(absorbance):
    return 4.171943074*absorbance - .1075750317 # best fit line

def acceptable_od(od):
    return od > .2

if __name__ == '__main__':
    local_log_dir = os.path.join(this_file_dir, 'log')
    if not os.path.exists(local_log_dir):
        os.mkdir(local_log_dir)
    main_logfile = os.path.join(local_log_dir, 'main.log')
    logging.basicConfig(filename=main_logfile, level=logging.DEBUG, format='[%(asctime)s] %(name)s %(levelname)s %(message)s')
    for banner_line in log_banner('Begin execution of ' + __file__):
        logging.info(banner_line)

    num_lagoons = 24
    lagoons = range(num_lagoons)
    num_reader_plates = 7
    num_tip_racks = 5
    turb_vol = 1000 # uL
    media_supply_vol = num_lagoons * 1.2 # mL
    turb_cycles_per_hour = 6
    fixed_turb_height = 9.0/1300*turb_vol # mm. measured height of 1.3mL in 24-well plate was 9mm. After changing turb_vol, linearly scaled.
    fly_disp_height = fixed_turb_height + 5 # mm
    no_bacteria_turbs = [0] # controls for whose OD we shouldn't wait to come up
    read_sample_vol = 100 # uL
    max_transfer_vol = 985 # uL
    rinse_cycles = 4
    cycle_replace_vol = 187.5 # uL
    generation_time = 60*30 # seconds
    fixed_lagoon_height = 19 # mm
    lagoon_fly_disp_height = fixed_lagoon_height + 15 # mm
    wash_vol = max_transfer_vol # uL
    
    # shaker parameters
    shaker_vortex_rpm = 800
    shaker_normal_shake_rpm = 400

    sys_state = types.SimpleNamespace()
    sys_state.done_equilibrating = False

    layfile = os.path.join(this_file_dir, '180703_24_personal_turbs.lay')
    lmgr = LayoutManager(layfile)

    lagoon_plate = lmgr.assign_unused_resource(ResourceType(Plate96, 'lagoons'))
    turb_plate = lmgr.assign_unused_resource(ResourceType(Plate96, 'turbidostats'))
    reader_plates_tl = resource_list_with_prefix(lmgr, 'reader_tl_', Plate96, num_reader_plates)
    reader_plates_tr = resource_list_with_prefix(lmgr, 'reader_tr_', Plate96, num_reader_plates)
    reader_plates_bl = resource_list_with_prefix(lmgr, 'reader_bl_', Plate96, num_reader_plates)
    reader_plates_br = resource_list_with_prefix(lmgr, 'reader_br_', Plate96, num_reader_plates)
    reader_plates = list(zip(reader_plates_tl, reader_plates_tr, reader_plates_bl, reader_plates_br))
    media_reservoir = lmgr.assign_unused_resource(ResourceType(Plate96, 'waffle'))
    turb_tips = lmgr.assign_unused_resource(ResourceType(Tip96, 'turbidostat_tips'))
    lagoon_tips = lmgr.assign_unused_resource(ResourceType(Tip96, 'lagoon_tips'))
    turb_tip_corral = lmgr.assign_unused_resource(ResourceType(Tip96, 'turbidostat_dirty_tips'))
    lagoon_tip_corral = lmgr.assign_unused_resource(ResourceType(Tip96, 'lagoon_dirty_tips')) 
    reader_tray = lmgr.assign_unused_resource(ResourceType(Plate96, 'reader_tray'))
    bleach_site = lmgr.assign_unused_resource(ResourceType(Tip96, 'RT300_HW_96WashDualChamber1_bleach'))
    rinse_site = lmgr.assign_unused_resource(ResourceType(Tip96, 'RT300_HW_96WashDualChamber1_water'))

    dummy_24_plate = Plate24('')

    def idx_24_to_96(idx):
        col96, row96 = (c*2 for c in dummy_24_plate.well_coords(idx)) # upsample a 6x4 well plate to be accessed by a 12x8 template
        return col96*8 + row96

    def tip_pos_for_lagoon(lagoon_idx):
        return lagoon_tips, idx_24_to_96(lagoon_idx)

    def corral_pos_for_lagoon(lagoon_idx):
        return lagoon_tip_corral, idx_24_to_96(lagoon_idx)

    def reader_plate_poss_gen():
        service_round = 0
        reader_plate_idx = 0
        offsets = [0, 8, 1, 9] # 96-indexed offsets to get cell and cells to right, bottom, and bottom-right
        while reader_plate_idx < len(reader_plates_tl):
            reader_plate_idx = service_round//4
            yield (reader_plates_tl[reader_plate_idx],
                reader_plates[reader_plate_idx][service_round%4],
                [idx_24_to_96(lagoon_idx) + offsets[service_round%4] for lagoon_idx in lagoons])
            service_round += 1

    reader_plate_poss_gen = reader_plate_poss_gen() # singleton

    def next_reader_plate_poss():
        try:
            return next(reader_plate_poss_gen)
        except StopIteration:
            return None

    def turb_pos_for_lagoon(lagoon_idx): # reasonable for now because there is one personal turbidostat per lagoon
        return turb_plate, idx_24_to_96(lagoon_idx)

    def turb_tip_pos_for_lagoon(lagoon_idx):
        return turb_tips, idx_24_to_96(lagoon_idx)

    def turb_corral_pos_for_lagoon(lagoon_idx):
        return turb_tip_corral, idx_24_to_96(lagoon_idx)

    def media_pos_for_lagoon(lagoon_idx):
        return media_reservoir, lagoon_idx%8 # aspirating from first column only for now

    def bleach_mounted_tips(ham_int, destination=None):
        logging.info('\n##### Bleaching currently mounted tips and depositing at ' + destination.layout_name())
        small_vol = 10
        logging.info('\n##### Refilling water and bleach.')
        wash_empty_refill(ham_int, refillAfterEmpty=1,
                                   chamber1WashLiquid=1, # 1=liquid 2 (blue container) (water)
                                   chamber2WashLiquid=1) # TODO: back to 0) # 0=Liquid 1 (red container) (bleach)
        logging.info('\n##### Bleaching.')
        aspirate_96(ham_int, bleach_site, small_vol, mixCycles=2, mixPosition=1, mixVolume=wash_vol, airTransportRetractDist=1)
        dispense_96(ham_int, bleach_site, small_vol, dispenseMode=9, liquidHeight=10) # mode: blowout
        logging.info('\n##### Rinsing.')
        aspirate_96(ham_int, rinse_site, wash_vol, mixCycles=rinse_cycles, mixPosition=1, mixVolume=wash_vol, airTransportRetractDist=1)
        dispense_96(ham_int, rinse_site, wash_vol, dispenseMode=9, liquidHeight=10) # mode: blowout
        if destination:
            tip_eject_96(ham_int, destination)
        logging.info('\n##### Done bleaching tips.')

    def absorbance_at(read_idx, abs_platedata, reader_plate):
        return abs_platedata.value_at(*reader_plate.well_coords(read_idx))

    def controller_replace_vol(lagoon, read_idx, abs_platedata, reader_plate): # convenience method, messy args but avoids a messy loop
        absorbance = absorbance_at(read_idx, abs_platedata, reader_plate)
        od = abs_to_od(absorbance)
        flow_rate_set = flow_rate_controller(od)
        logging.info('Turb for lagoon ' + str(lagoon) + ': Plate ' + reader_plate.layout_name() +
                ', well ' + reader_plate.position_id(read_idx) + ', absorbance ' + str(absorbance) +
                ', OD ' + str(od) + ', flow rate setting ' + str(flow_rate_set))
        return flow_rate_set*turb_vol/turb_cycles_per_hour

    def reader_plate_id(reader_plate):
        return __file__ + ' plate ' + str(reader_plates_tl.index(reader_plate))

    turb_shaker = Shaker()

    def service_turbidostats(ham_int, pump_int, reader_int):
        logging.info('\n##### ---------------- Servicing turbidostats ----------------')
        
        logging.info('\n##### ----- Turbidostats ' + ', '.join((str(l) for l in lagoons)) + ' -----')

        logging.info('\n##### Sampling liquid from turbidostats into reader plates.')
        tip_pick_up_96(ham_int, turb_tips)
        turb_shaker.stop()
        aspirate_96(ham_int, turb_plate, read_sample_vol, liquidFollowing=1, liquidHeight=(fixed_turb_height - 3))
        turb_shaker.start(shaker_normal_shake_rpm)
        reader_plate, reader_plate_site, well_idxs = next_reader_plate_poss()
        dispense_96(ham_int, reader_plate_site, read_sample_vol, liquidHeight=5, dispenseMode=9) # mode: blowout
        # TODO tip_eject_96(ham_int, turb_tip_corral) # need to eject tips before read
        media_fill_thread = run_async(lambda:(
            logging.info('\n##### Asynchronously bleaching LB reservoir.'),
            pump_int.bleach_clean(),
            logging.info('\n##### Asynchronously refilling LB reservoir.'),
            pump_int.refill(media_supply_vol))) # clean and refill media sequentially, asynchronously
        def async_bleach():
            # TODO tip_pick_up_96(ham_int, turb_tip_corral)
            bleach_mounted_tips(ham_int, destination=turb_tips)
        abs_platedata, = read_plate(ham_int, reader_int, reader_tray, reader_plate, ['17_8_12_abs'],
                plate_id=reader_plate_id(reader_plate), async_task=async_bleach) # meanwhile, asynchronously bleach
        if simulation_on:
            abs_platedata = PlateData(os.path.join(reader_results_dir, '17_8_12_abs_180426_1910.csv')) # sim dummy
        abs_platedata.wait_for_file()
        db_add_plate_data(abs_platedata, 'abs', reader_plate, vessel_numbers=lagoons, read_wells=well_idxs, vessel_type='turbidostat')

        ## Perform turbidostat dilution liquid transfers
        logging.info('\n##### Moving fresh LB into turbidostats.')
        all_ods_acceptable = True
        for batch in yield_in_chunks(zip(lagoons, well_idxs), 8):
            lagoon_batch, idx_batch = zip(*batch)
            batch_tips = [turb_tip_pos_for_lagoon(l) for l in lagoon_batch]
            batch_turbs = [turb_pos_for_lagoon(l) for l in lagoon_batch]
            batch_media_poss = [media_pos_for_lagoon(l) for l in lagoon_batch]
            batch_corral_poss = [turb_corral_pos_for_lagoon(l) for l in lagoon_batch]
            replace_vols = [controller_replace_vol(l, well_idx, abs_platedata, reader_plate) for l, well_idx in zip(lagoon_batch, idx_batch)]
            tip_pick_up(ham_int, batch_tips)
            add_vols = [max(read_sample_vol*2, v) for v in replace_vols] # make sure there's more than enough liquid to read next time
            media_fill_thread.join() # make sure media is there
            aspirate(ham_int, batch_media_poss, add_vols, liquidHeight=1)
            turb_shaker.stop()
            dispense(ham_int, batch_turbs, add_vols, liquidHeight=fly_disp_height, dispenseMode=9) # mode: blowout
            turb_shaker.start(shaker_normal_shake_rpm)
            tip_eject(ham_int, batch_corral_poss)
            all_ods_acceptable = all_ods_acceptable and all((acceptable_od(abs_to_od(absorbance_at(well_idx, abs_platedata, reader_plate)))
                                                                                                                for well_idx in idx_batch))
        if not sys_state.done_equilibrating and all_ods_acceptable:
            logging.info('\n##### >>>>>>>>>> Turbidostats have equilibrated! <<<<<<<<<<')
            sys_state.done_equilibrating = True # latch True for remainder of experiment

        logging.info('\n##### Removing liquid from turbidostats down to constant volume.')
        tip_pick_up_96(ham_int, turb_tip_corral)
        turb_shaker.stop()
        excess_vol = max_transfer_vol*.8
        aspirate_96(ham_int, turb_plate, excess_vol, liquidHeight=fixed_turb_height)
        vortex_thread = run_async(lambda:(
            turb_shaker.start(shaker_vortex_rpm),
            time.sleep(3.5),
            turb_shaker.start(shaker_normal_shake_rpm)))
        dispense_96(ham_int, bleach_site, excess_vol, liquidHeight=10, dispenseMode=9) # mode: blowout

        logging.info('\n##### Bleaching tips and re-racking.')
        bleach_mounted_tips(ham_int, destination=turb_tips)

        vortex_thread.join()
        logging.info('\n##### ------------- Done servicing turbidostats -------------\n')

    def service_lagoons(ham_int, pump_int, reader_int):
        if not sys_state.done_equilibrating:
            logging.info('\n\n##### ------ Not yet equilibrated, lagoons not serviced ------\n')
            return
        logging.info('\n\n##### ------------------ Servicing lagoons ------------------')

        logging.info('\n##### Moving fresh bacteria into lagoons.')
        tip_pick_up_96(ham_int, lagoon_tips)
        turb_shaker.stop()
        aspirate_96(ham_int, turb_plate, cycle_replace_vol, liquidHeight=4)
        turb_shaker.start(shaker_normal_shake_rpm)
        dispense_96(ham_int, lagoon_plate, cycle_replace_vol,liquidHeight=lagoon_fly_disp_height, dispenseMode=9) # mode: blowout

        logging.info('\n##### Removing liquid from lagoons to reader plates')
        aspirate_96(ham_int, lagoon_plate, read_sample_vol, mixCycles=2, mixPosition=2,
                mixVolume=500, liquidFollowing=1, liquidHeight=fixed_lagoon_height-3)
        excess_vol = max_transfer_vol*.8
        reader_plate, reader_plate_site, well_idxs = next_reader_plate_poss()
        dispense_96(ham_int, reader_plate_site, read_sample_vol, liquidHeight=5, dispenseMode=9) # mode: blowout
        aspirate_96(ham_int, lagoon_plate, excess_vol, liquidHeight=fixed_lagoon_height)
        dispense_96(ham_int, bleach_site, excess_vol, liquidHeight=10, dispenseMode=9) # mode: blowout
        # TODO tip_eject_96(ham_int, lagoon_tip_corral) # need to eject tips before read
        plate_id = reader_plate_id(reader_plate)
        protocols = ['17_8_12_lum', '17_8_12_abs']
        data_types = ['lum', 'abs']
        def async_bleach():
            # TODO tip_pick_up_96(ham_int, lagoon_tip_corral)
            bleach_mounted_tips(ham_int, destination=lagoon_tips)
        platedatas = read_plate(ham_int, reader_int, reader_tray, reader_plate, protocols, plate_id, async_task=async_bleach) # meanwhile, asynchronously bleach
        if simulation_on:
            platedatas = [PlateData(os.path.join(reader_results_dir, '17_8_12_abs_180426_1910.csv'))]*2 # sim dummies
        for platedata, data_type in zip(platedatas, data_types):
            platedata.wait_for_file()
            db_add_plate_data(platedata, data_type, reader_plate, lagoons, well_idxs, 'lagoon')
        reader_int.plate_in(block=False)
        logging.info('\n##### --------------- Done servicing lagoons ---------------\n')

    disable_pumps = '--no_pumps' in sys.argv
    simulation_on = '--simulate' in sys.argv
    sys_state.done_equilibrating = '--no_equilibribus' in sys.argv

    def times_at_intervals(interval):
        target_time = time.time()
        while True:
            yield target_time
            target_time += 1 if simulation_on else interval

    schedule_items = [ # tuples (function to schedule, monotonic absolute time generator)
        (service_lagoons, times_at_intervals(generation_time)),
        (service_turbidostats, times_at_intervals(3600/turb_cycles_per_hour))
        ]

    with HamiltonInterface(simulate=simulation_on) as ham_int, LBPumps() as pump_int, ClarioStar() as reader_int:
        if disable_pumps or simulation_on:
            pump_int.disable()
        if simulation_on:
            reader_int.disable()
            turb_shaker.disable()
        ham_int.set_log_dir(os.path.join(local_log_dir, 'hamilton.log'))
        init_cmd = initialize(ham_int, async=True)
        turb_shaker.start(400)
        logging.info('\n##### Priming pump lines.')
        pump_int.prime()
        ham_int.wait_on_response(init_cmd, raise_first_exception=True)
        hepa_on(ham_int, simulate=int(simulation_on))

        start_time = time.time()
        next_times = {}
        while True:
            for task_num, (scheduled_func, interval_gen) in enumerate(schedule_items):
                if task_num not in next_times:
                    next_times[task_num] = next(interval_gen)
                next_time = next_times[task_num]
                if time.time() - next_time >= 0:
                    scheduled_func(ham_int, pump_int, reader_int)
                    try:
                        next_times[task_num] = next(interval_gen)
                    except StopIteration:
                        break
            else:
                time.sleep(.2)
                continue
            break
