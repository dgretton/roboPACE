import sqlite3
import matplotlib.pyplot as plt
from datetime import datetime
import sys
'''
from importlib import import_module
method_module = import_module('180518_personal_turbs_shaker_mods')

if '--controller' in sys.argv:
    max_od = 2.0
    x_pts = [x/100 for x in range(int(100*2.0))]
    plt.plot(x_pts, [method_module.flow_rate_controller(x) for x in x_pts])
    plt.show()
    exit()
'''
number_of_turb = 3

conn = sqlite3.connect('180518_personal_turbs_shaker_mods.db')
c = conn.cursor()


for plot_type in ['lagoon', 'turbidostat']:    
    scale = 0.8
    fig1 = plt.figure(figsize=(30*scale, 6*scale))
    for turb in range(number_of_turb):
        # set up plot
        ax = fig1.add_subplot(1, number_of_turb, turb+1)
        ax.set_title(plot_type + str(turb), x=0.5, y=0.8)

        if plot_type == 'turbidostat':    
            n = (turb, 'abs', )
            c.execute('SELECT filename, well, reading FROM measurements WHERE turb_number=? AND data_type=?', n)
        else:
            n = (turb, 'lum', )
            c.execute('SELECT filename, well, reading FROM measurements WHERE lagoon_number=? AND data_type=?', n)
       
        x = c.fetchall()
        print(len(x), "entries fetched")
        vals = [(datetime.strptime(f[-15:-4], '%y%m%d_%H%M'), w, v) for (f, w, v) in x]
        vals = [(t, w, v) for t, w, v in vals if t > datetime(2018, 4, 27, 0, 0)]
        
        
        if plot_type == 'turbidostat':   
            plt.plot([j for (j, _, _) in vals], [4.171943074*abs - .1075750317 for (_, _, abs) in vals], 'r.-') # OD conversion formula
            plt.ylim(0.0, 2.0)
        else:
            plt.plot([j for (j, _, _) in vals], [lum for (j, _, lum) in vals])
            plt.ylim(0.0, 250.0)
            
        # decrease number of plotted X axis labels
        # make there be fewer labels so that you can read them
        times = [x for (x, _, _) in vals]
        deltas = [t - times[0] for t in times]
        labels = [int(d.seconds/60/60 + d.days*24) for d in deltas]
        labels_sparse = [labels[x] if x % 12 == 0 else '' for x in range(len(labels))]
        plt.xticks(times, labels_sparse)
        locs, labels = plt.xticks()

    
    fig1.tight_layout()
    plt.savefig('5_21_' + plot_type + '_plot.png', dpi = 200)

# We can also close the connection if we are done with it.
# Just be sure any changes have been committed or they will be lost.
conn.close()