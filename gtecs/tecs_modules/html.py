#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                                html.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#    G-TeCS module containing html page functions for the scheduler    #
#                     Martin Dyer, Sheffield, 2016                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
from astroplan import Observer, FixedTarget, is_observable
from astroplan.moon import moon_illumination
from astropy import coordinates as coord, units as u
from astropy.time import Time

# TeCS modules
from . import params
from . import astronomy
from .. import database as db

## setup
# common strings
html_size2   = '<font size=2 color=black face=\"Courier New\">\n'
html_size5   = '<font size=5 color=black face=\"Courier New\">\n'
popup_str = ('<div class=\"apple_overlay\" id=\"overlay\">' +
            '<div class=\"contentWrap\"></div>' + '</div>')

# set observing location
GOTO = params.SITE_OBSERVER

# set debug level
debug = 1

# define paths
html_folder = params.CONFIG_PATH + 'html/'
queue_file   = params.QUEUE_PATH  + 'queue_info'

# interval between automatic refreshes
html_refresh = 15
html_refresh_string = ('<meta http-equiv=\"refresh\" content=\"' +
                      str(html_refresh) + '\">\n')


def import_queue_file():
    import json
    lines = []
    with open(queue_file) as f:
        for line in f.readlines():
            lines.append(line)

    time = Time(json.loads(lines[0]))
    all_constraint_names = json.loads(lines[1])
    # remaining lines are pointings
    pointing_list = []
    for line in lines[2:]:
        pointingID, priority, constraints = json.loads(line)
        pointingID = int(pointingID)
        priority = float(priority)
        constraint_names, valid_arr = list(zip(*constraints))
        valid_bools = [bool(x) for x in valid_arr]
        pointing_list.append([pointingID, priority, list(constraint_names), valid_bools])
    return time, all_constraint_names, pointing_list


def write_flag_file(dbPointing, time, all_constraint_names, pointing_info):
    '''Write flag file for a given pointing'''
    pointingID, priority_now, constraint_names, valid_arr = pointing_info
    flag_filename = html_folder + 'ID_{}_flags.html'.format(pointingID)

    with open(flag_filename,'w') as f:
        f.write('<html><body>\n')
        f.write(html_size2)
        f.write('ID_%i<br>\n' %pointingID)

        time.format = 'iso'
        time.precision = 0
        f.write(str(time) + '<br>\n')

        for name, valid in zip(constraint_names, valid_arr):
            if valid:
                f.write('<font color=darkgreen>')
            else:
                f.write('<font color=red>')
            f.write(name + ' = ' + str(valid))
            f.write('</font><br>\n')

        for name in all_constraint_names:
            if name not in constraint_names:
                f.write('<font color=grey>')
                f.write(name + ' = N/A')
                f.write('</font><br>\n')

        f.write('<br>\n')

        if debug == 1:
            f.write('Debug info:<br>\n')
            f.write('now = ' + str(time) + '<br>\n')
            start = Time(dbPointing.startUTC)
            start.format = 'iso'
            start.precision = 0
            f.write('start_time = ' + str(start) + '<br>\n')
            stop = Time(dbPointing.stopUTC)
            stop.format = 'iso'
            stop.precision = 0
            f.write('stop_time = ' + str(stop) + '<br>\n')

            target = coord.SkyCoord(ra=dbPointing.ra, dec=dbPointing.decl,
                                    unit=u.deg, frame='icrs')
            ra = target.ra.to_string(sep=':', precision=2, unit=u.hour)
            dec = target.dec.to_string(sep=':', precision=2)
            f.write('ra = ' + ra + '<br>\n')
            f.write('dec = ' + dec + '<br>\n')

            alt_now, az_now = astronomy.altaz_ephem(target.ra.value,
                                                    target.dec.value,
                                                    time)

            f.write('alt_now = ' + str(alt_now) + '<br>\n')
            f.write('az_now = ' + str(az_now) + '<br>\n')

            alt_later, az_later = astronomy.altaz_ephem(target.ra.value,
                                                        target.dec.value,
                                                        time + dbPointing.minTime*u.s)

            f.write('alt_mintime = ' + str(alt_later) + '<br>\n')
            f.write('az_mintime = ' + str(az_later) + '<br>\n')

            #altart
            #altart_mintime

            #moondist
            #sunalt_mintime
        f.write("</body></html>")


def write_exp_file(pointingID, dbExps):
    '''Write exposure files for a pointing'''
    exp_filename = html_folder + 'ID_{}_exp.html'.format(pointingID)
    with open(exp_filename,'w') as f:
        f.write('<html><body><table cellpadding=5 cellspacing=5>\n')
        f.write('<tr>' +
                '<td><b> </b></td>' +
                '<td><b>Tels</b></td>' +
                '<td><b>NumExp</b></td>' +
                '<td><b>Exptime</b></td>' +
                '<td><b>Filter</b></td>' +
                '<td><b>Bin Factor</b></td>' +
                '<td><b>Type</b></td>' +
                '</tr>\n')
        i = 1
        for dbExp in dbExps:
            f.write('<tr>' +
                    '<td align=right><b>' + str(i) + '</b></td>' +
                    '<td> ' + str(dbExp.otaMask) + ' </td>' +
                    '<td> ' + str(dbExp.numexp) + ' </td>' +
                    '<td> ' + str(dbExp.expTime) + ' </td>' +
                    '<td> ' + str(dbExp.filt) + ' </td>' +
                    '<td> ' + str(dbExp.binning) + ' </td>' +
                    '<td> ' + str(dbExp.typeFlag) + ' </td>' +
                    '</tr>\n')
            i += 1
        f.write("</table></body></html>")


def write_queue_page(pointinglist, current_pointing, now):
    '''Write the GOTO queue page'''
    queue_filename = html_folder + 'queue.html'
    with open(queue_filename,'w') as f:
        f.write('<html><head>\n')
        f.write('<script src=\"jquery.tools.min.js\"></script>' +
                '<link rel=\"stylesheet\" type=\"text/css\" ' +
                'href=\"overlay-apple.css\"/>\n')
        f.write('<style>#overlay {background-image:url(transparent.png); ' +
                'color:#efefef;height:450px;}'+
                'div.contentWrap {height:441px;overflow-y:auto;}</style>\n')
        f.write(html_refresh_string)
        f.write('<style type=\"text/css\"><!-- ' +
                'td{font-family: arial; font-size: 10pt; color: black; ' +
                'white-space: nowrap;} tr{background-color: white;} --->' +
                '</style>\n')
        f.write('<title>GOTO queue</title></head>\n')
        f.write('<body bgcolor=white vlink=black alink=black link=black>\n')
        f.write('<center><br><br><img src=goto-logo.jpg width=50%><br><br>\n')

        f.write(html_size5)
        f.write('queue')
        f.write(html_size2)
        f.write('<br><br>')
        f.write('last updated:\n')
        f.write(str(now))
        f.write('<br>\n')

        loc = params.SITE_OBSERVER.location
        import warnings
        warnings.simplefilter("ignore", UnicodeWarning)
        f.write('LST: ')
        LST = now.sidereal_time('mean', longitude=loc.longitude)
        f.write(LST.to_string(sep=':', precision=2))

        f.write('  SunAlt: ')
        sun = coord.get_sun(now)
        sun_alt, _ = astronomy.altaz_ephem(sun.ra.value, sun.dec.value, now)
        f.write('%.1f deg' %sun_alt)

        f.write('  MoonAlt: ')
        moon = coord.get_moon(now)
        moon_alt, _ = astronomy.altaz_ephem(moon.ra.value, moon.dec.value, now)
        f.write('%.1f deg' %moon_alt)

        f.write('  MoonPhase: ')
        moon_ill = moon_illumination(now, params.SITE_OBSERVER.location)
        if 0 <= moon_ill < 0.25:
            moonstring = "D"
        elif 0.25 <= moon_ill < 0.65:
            moonstring = "G"
        elif 0.65 <= moon_ill <= 1.00:
            moonstring = "B"
        if moon_alt < params.MOONELEV_LIMIT:
            moonstring = "D"
        f.write('%.2f [%s]' %(moon_ill, moonstring))

        f.write('<br><br>' +
                '<table border=1 bordercolor=#00639C cellspacing=\"0\"' +
                ' cellpadding=\"10\"><tr bgcolor=white>\n')
        f.write('<td><b> Target name </b></td>' +
                '<td><b> RA (j2000) </b></td>' +
                '<td><b> Dec (j2000) </b></td>' +
                '<td><b> Priority </b></td>' +
                '<td><b> MaxSun </b></td>' +
                '<td><b> MinAlt </b></td>' +
                '<td><b> MinTime </b></td>' +
                '<td><b> MaxMoon </b></td>' +
                '<td><b> User </b></td>' +
                '<td><b> Start UTC </b></td>' +
                '<td><b> Stop UTC </b></td>' +
                '<td><b> Filename/Flags </b></td>' +
                '</tr>\n')

        for pointing in pointinglist:
            exp_link = 'ID_{}_exp.html'.format(pointing.id)
            flag_link = 'ID_{}_flags.html'.format(pointing.id)

            exp_str = ('<a href=' + exp_link + ' rel=\"#overlay\">' +
                        str(pointing.name) + '</a>' + popup_str)
            flag_str = ('<a href=' + flag_link + ' rel=\"#overlay\">' +
                        'ID_' + str(pointing.id) + '</a>' + popup_str)

            if pointing == current_pointing:
                priority_str = "<font color=limegreen>%.11f</font>" % pointing.priority_now
            elif pointing.priority_now < 100:
                priority_str = "<font color=black>%.11f</font>" % pointing.priority_now
            else:
                priority_str = "<font color=red>%.11f</font>" % pointing.priority_now
            ra = pointing.coord.ra.to_string(sep=':', precision=2, unit=u.hour)
            dec = pointing.coord.dec.to_string(sep=':', precision=2)
            f.write("<tr bgcolor=white>\n")
            f.write('<td>' + exp_str + '</td>' +
                    '<td>' + ra + '</td>' +
                    '<td>' + dec + '</td>' +
                    '<td>' + priority_str + '</td>' +
                    '<td>' + str(pointing.maxsunalt) + '</td>' +
                    '<td>' + str(pointing.minalt) + '</td>' +
                    '<td>' + str(pointing.mintime) + '</td>' +
                    '<td>' + str(pointing.maxmoon) + '</td>' +
                    '<td>' + str(pointing.user) + '</td>' +
                    '<td>' + str(pointing.start) + '</td>' +
                    '<td>' + str(pointing.stop) + '</td>' +
                    '<td>' + flag_str + '</td>' +
                    '</tr>\n')
            f.write("</tr>\n")
        f.write("</table>\n")
        f.write('<br><br></center>\n')
        f.write('<script>$(function() ' +
                '{$(\"a[rel]\").overlay({mask: \'black\',effect: \'apple\',' +
                'onBeforeLoad: function() ' +
                '{var wrap = this.getOverlay().find(\".contentWrap\");' +
                'wrap.load(this.getTrigger().attr(\"href\"));}});});' +
                '</script>\n')

        f.write('</body></html>\n')
