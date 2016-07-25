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
from astropy import coordinates as coord, units as u

# TeCS modules
from . import params
from . import astronomy

## setup
# common strings
html_size2   = '<font size=2 color=black face=\"Courier New\">\n'
html_size5   = '<font size=5 color=black face=\"Courier New\">\n'
popup_str = ('<div class=\"apple_overlay\" id=\"overlay\">' +
            '<div class=\"contentWrap\"></div>' + '</div>')

# define paths
html_folder = params.CONFIG_PATH + 'html/'

# interval between automatic refreshes
html_refresh = 15
html_refresh_string = ('<meta http-equiv=\"refresh\" content=\"' +
                      str(html_refresh) + '\">\n')


def write_obs_flag_files(obs, now, observer, debug):
    '''Write flag files for an observation'''
    flag_filename = html_folder + 'ID_{}_flags.html'.format(obs.id)

    now.format = 'iso'
    now.precision = 0

    target = [obs._as_target()]

    with open(flag_filename,'w') as f:
        f.write('<html><body>\n')
        f.write(html_size2)
        f.write('ID_' + str(obs.id) + '<br>\n')
        f.write(str(now) + '<br>\n')

        for name, valid in zip(obs.constraint_names, obs.valid_now_arr):
            if valid:
                f.write('<font color=darkgreen>')
            else:
                f.write('<font color=red>')
            f.write(name + ' = ' + str(valid))
            f.write('</font><br>\n')

        for name, valid_later in zip(obs.mintime_constraint_names,
                                     obs.valid_later_arr):
            later = now + obs.mintime
            if obs.priority >= 5:
                f.write('<font color=grey>')
                valid_later = 'N/A'
            elif valid_later:
                f.write('<font color=darkgreen>')
            else:
                f.write('<font color=red>')
            f.write(name + ' = ' + str(valid_later))
            f.write('</font><br>\n')

        f.write('<br>\n')
        if debug == 1:
            f.write('Debug info:<br>\n')
            f.write('now = ' + str(now) + '<br>\n')
            start = obs.start
            start.format = 'iso'
            start.precision = 0
            f.write('start_time = ' + str(obs.start) + '<br>\n')
            stop = obs.stop
            stop.format = 'iso'
            stop.precision = 0
            f.write('stop_time = ' + str(obs.stop) + '<br>\n')

            f.write('ra = ' + str(obs.coord.ra) + '<br>\n')
            f.write('dec = ' + str(obs.coord.dec) + '<br>\n')

            alt_now, az_now = astronomy.altaz(obs.coord.ra.value,
                                              obs.coord.dec.value,
                                              now)

            f.write('alt_now = ' + str(alt_now) + '<br>\n')
            f.write('az_now = ' + str(az_now) + '<br>\n')

            alt_later, az_later = astronomy.altaz(obs.coord.ra.value,
                                                  obs.coord.dec.value,
                                                  now + obs.mintime)

            f.write('alt_mintime = ' + str(alt_later) + '<br>\n')
            f.write('az_mintime = ' + str(az_later) + '<br>\n')

            #altart
            #altart_mintime

            #moondist
            #sunalt_mintime
        f.write("</body></html>")


def write_obs_exp_files(obs):
    '''Write exposure set files for an observation'''
    exp_filename = html_folder + 'ID_{}_exp.html'.format(obs.id)
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
        for expset in obs.exposuresets:
            f.write('<tr>' +
                    '<td align=right><b>' + str(i) + '</b></td>' +
                    '<td> ' + str(expset.tels) + ' </td>' +
                    '<td> ' + str(expset.numexp) + ' </td>' +
                    '<td> ' + str(expset.exptime) + ' </td>' +
                    '<td> ' + str(expset.filt) + ' </td>' +
                    '<td> ' + str(expset.binfac) + ' </td>' +
                    '<td> ' + str(expset.exptype) + ' </td>' +
                    '</tr>\n')
            i += 1
        f.write("</table></body></html>")


def write_queue_page(obslist_sorted, obs_now, now):
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
        f.write('<br><br>' +
                '<table border=1 bordercolor=#00639C cellspacing=\"0\"' +
                ' cellpadding=\"10\"><tr bgcolor=white>\n')
        f.write('<td><b> Target name </b></td>' +
                '<td><b> RA (j2000) </b></td>' +
                '<td><b> Dec (j2000) </b></td>' +
                '<td><b> Priority </b></td>' +
                '<td><b> MaxSun </b></td>' +
                '<td><b> MinAlt (deg) </b></td>' +
                '<td><b> MinTime (s) </b></td>' +
                '<td><b> MaxMoon </b></td>' +
                '<td><b> User </b></td>' +
                '<td><b> Start UTC </b></td>' +
                '<td><b> Stop UTC </b></td>' +
                '<td><b> Filename/Flags </b></td>' +
                '</tr>\n')

        for obs in obslist_sorted:
            exp_link = 'ID_{}_exp.html'.format(obs.id)
            flag_link = 'ID_{}_flags.html'.format(obs.id)

            exp_str = ('<a href=' + exp_link + ' rel=\"#overlay\">' +
                        str(obs.name) + '</a>' + popup_str)
            flag_str = ('<a href=' + flag_link + ' rel=\"#overlay\">' +
                        'ID_' + str(obs.id) + '</a>' + popup_str)

            if obs == obs_now:
                priority_str = "<font color=limegreen>%.5f</font>" % obs.priority_now
            elif obs.priority_now < 10:
                priority_str = "<font color=black>%.5f</font>" % obs.priority_now
            else:
                priority_str = "<font color=red>%.5f</font>" % obs.priority_now
            f.write("<tr bgcolor=white>\n")
            f.write('<td>' + exp_str + '</td>' +
                    '<td>' + str(obs.coord.ra) + '</td>' +
                    '<td>' + str(obs.coord.dec) + '</td>' +
                    '<td>' + priority_str + '</td>' +
                    '<td>' + str(obs.maxsunalt) + '</td>' +
                    '<td>' + str(obs.minalt) + '</td>' +
                    '<td>' + str(obs.mintime) + '</td>' +
                    '<td>' + str(obs.maxmoon) + '</td>' +
                    '<td>' + str(obs.user) + '</td>' +
                    '<td>' + str(obs.start) + '</td>' +
                    '<td>' + str(obs.stop) + '</td>' +
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
