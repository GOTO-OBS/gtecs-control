"""
HTML page functions for the scheduler
"""

import os

from astropy import coordinates as coord, units as u
from astropy.time import Time

from astroplan import Observer, FixedTarget, is_observable
from astroplan.moon import moon_illumination

import obsdb as db

from . import params
from . import astronomy


## setup
# common strings
html_size2   = '<font size=2 color=black face=\"Courier New\">\n'
html_size5   = '<font size=5 color=black face=\"Courier New\">\n'
popup_str = ('<div class=\"apple_overlay\" id=\"overlay\">' +
            '<div class=\"contentWrap\"></div>' + '</div>')

# set observing location
GOTO = Observer(astronomy.observatory_location())

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
        pointingID, priority, altaznow, altazlater, constraints = json.loads(line)
        pointingID = int(pointingID)
        priority = float(priority)
        constraint_names, valid_arr = list(zip(*constraints))
        valid_bools = [bool(x) for x in valid_arr]
        pointing_info =[pointingID, priority,
                        altaznow, altazlater,
                        list(constraint_names), valid_bools]
        pointing_list.append(pointing_info)
    return time, all_constraint_names, pointing_list


def write_flag_file(dbPointing, time, all_constraint_names, pointing_info):
    """Write flag file for a given pointing"""
    pointingID, priority_now, altaznow, altazlater, constraint_names, valid_arr = pointing_info
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
            if dbPointing.stopUTC:
                stop = Time(dbPointing.stopUTC)
                stop.format = 'iso'
                stop.precision = 0
            else:
                stop = 'None'
            f.write('stop_time = ' + str(stop) + '<br>\n')

            target = coord.ICRS(dbPointing.ra*u.deg, dbPointing.decl*u.deg)
            ra = target.ra.to_string(sep=':', precision=2, unit=u.hour)
            dec = target.dec.to_string(sep=':', precision=2)
            f.write('ra = ' + ra + '<br>\n')
            f.write('dec = ' + dec + '<br>\n')

            alt_now, az_now = altaznow
            f.write('alt_now = %.2f<br>\n' %alt_now)
            f.write('az_now = %.2f<br>\n' %az_now)

            alt_later, az_later = altazlater
            f.write('alt_mintime = %.2f<br>\n' %alt_later)
            f.write('az_mintime = %.2f<br>\n' %az_later)

            #altart
            #altart_mintime

            #moondist
            #sunalt_mintime
        f.write("</body></html>")


def write_exp_file(pointingID, dbExps):
    """Write exposure files for a pointing"""
    exp_filename = html_folder + 'ID_{}_exp.html'.format(pointingID)

    # unlike the flags, exposure info dosn't change
    # so don't re-write the files if they're already there!
    if not os.path.exists(exp_filename):
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
                    '<td><b>RaOff</b></td>' +
                    '<td><b>DecOff</b></td>' +
                    '</tr>\n')
            i = 1
            for dbExp in dbExps:
                f.write('<tr>' +
                        '<td align=right><b>' + str(i) + '</b></td>' +
                        '<td> ' + str(dbExp.utMask) + ' </td>' +
                        '<td> ' + str(dbExp.numexp) + ' </td>' +
                        '<td> ' + str(dbExp.expTime) + ' </td>' +
                        '<td> ' + str(dbExp.filt) + ' </td>' +
                        '<td> ' + str(dbExp.binning) + ' </td>' +
                        '<td> ' + str(dbExp.typeFlag) + ' </td>' +
                        '<td> ' + str(dbExp.raoff) + ' </td>' +
                        '<td> ' + str(dbExp.decoff) + ' </td>' +
                        '</tr>\n')
                i += 1
            f.write("</table></body></html>")


def write_queue_page():
    """Write the GOTO queue page"""
    # load any needed infomation saved by the scheduler
    time, all_constraint_names, pointing_list = import_queue_file()

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
        time.format = 'iso'
        time.precision = 0
        f.write(str(time))
        f.write('<br>\n')

        f.write('LST: ')
        import warnings
        warnings.simplefilter("ignore", UnicodeWarning)
        LST = time.sidereal_time('mean', longitude=GOTO.location.lon)
        f.write(LST.to_string(sep=':', precision=2))

        f.write('  SunAlt: ')
        sun = coord.get_sun(time)
        sun_alt, _ = astronomy.altaz_from_radec(sun.ra.value, sun.dec.value, time)
        f.write('%.1f deg' %sun_alt)

        f.write('  MoonAlt: ')
        moon = coord.get_moon(time)
        moon_alt, _ = astronomy.altaz_from_radec(moon.ra.value, moon.dec.value, time)
        f.write('%.1f deg' %moon_alt)

        f.write('  MoonPhase: ')
        moon_ill = moon_illumination(time)
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
                '<td><b> MinTime </b></td>' +
                '<td><b> MinAlt </b></td>' +
                '<td><b> MaxSun </b></td>' +
                '<td><b> MaxMoon </b></td>' +
                '<td><b> MinMoonSep </b></td>' +
                '<td><b> User </b></td>' +
                '<td><b> Start UTC </b></td>' +
                '<td><b> Stop UTC </b></td>' +
                '<td><b> Filename/Flags </b></td>' +
                '</tr>\n')

        for pointing_info in pointing_list:
            pointingID   = pointing_info[0]

            # find database info
            session = db.load_session()
            dbPointing = db.get_pointing_by_id(session, pointingID)
            if dbPointing.exposure_sets is not None:
                dbExps = dbPointing.exposure_sets
            username = db.get_username(session, dbPointing.userKey)
            session.close()

            # create the small pointing files
            write_flag_file(dbPointing, time, all_constraint_names, pointing_info)
            flag_link = 'ID_{}_flags.html'.format(pointingID)
            flag_str = ('<a href=' + flag_link + ' rel=\"#overlay\">' +
                        'ID_' + str(pointingID) + '</a>' + popup_str)

            write_exp_file(pointingID, dbExps)
            exp_link = 'ID_{}_exp.html'.format(pointingID)
            exp_str = ('<a href=' + exp_link + ' rel=\"#overlay\">' +
                        str(dbPointing.objectName) + '</a>' + popup_str)

            # find priority
            priority_now = pointing_info[1]
            if dbPointing.status == 'running':
                priority_str = "<font color=limegreen>%.11f</font>" %priority_now
            elif priority_now < 100:
                priority_str = "<font color=black>%.11f</font>" %priority_now
            else:
                priority_str = "<font color=red>%.11f</font>" %priority_now

            # find ra/dec
            target = coord.ICRS(dbPointing.ra*u.deg, dbPointing.decl*u.deg)
            ra = target.ra.to_string(sep=':', precision=2, unit=u.hour)
            dec = target.dec.to_string(sep=':', precision=2)

            # write table
            f.write("<tr bgcolor=white>\n")
            f.write('<td>' + exp_str + '</td>' +
                    '<td>' + ra + '</td>' +
                    '<td>' + dec + '</td>' +
                    '<td>' + priority_str + '</td>' +
                    '<td>' + str(dbPointing.minTime) + '</td>' +
                    '<td>' + str(dbPointing.minAlt) + '</td>' +
                    '<td>' + str(dbPointing.maxSunAlt) + '</td>' +
                    '<td>' + str(dbPointing.maxMoon) + '</td>' +
                    '<td>' + str(dbPointing.minMoonSep) + '</td>' +
                    '<td>' + str(username) + '</td>' +
                    '<td>' + str(dbPointing.startUTC) + '</td>' +
                    '<td>' + str(dbPointing.stopUTC) + '</td>' +
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
