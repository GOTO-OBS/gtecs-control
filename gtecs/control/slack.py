"""Slack messaging tools."""

import astropy.units as u
from astropy.time import Time

from gtecs.common.slack import send_message

from . import params
from .astronomy import night_startdate, sunalt_time
from .flags import Conditions, Status


def send_slack_msg(text, channel=None, tel_name=True, *args, **kwargs):
    """Send a message to Slack.

    Parameters
    ----------
    text : string
        The message text.
    channel : string, optional
        The channel to post the message to.
        If None, defaults to `gtecs.control.params.SLACK_DEFAULT_CHANNEL`.
    tel_name : bool, default=True
        If True then prepend each message with `params.TELESCOPE_NAME`

    Other parameters are passed to `gtecs.common.slack.send_slack_msg`.

    """
    if channel is None:
        channel = params.SLACK_DEFAULT_CHANNEL

    if tel_name:
        # Add the telescope name before the message
        text = f'{params.TELESCOPE_NAME}: ' + text

    if params.ENABLE_SLACK:
        # Use the common function
        send_message(text, channel, params.SLACK_BOT_TOKEN, *args, **kwargs)
    else:
        print('Slack Message:', text)


def send_conditions_report(slack_channel=None, site=params.SITE_NAME):
    """Send a Slack message with the current conditions, status and webcams."""
    blocks = []
    attachments = []

    # Conditions summary
    conditions = Conditions()
    if conditions.bad:
        conditions_status = ':warning: Conditions are bad! :warning:'
    else:
        conditions_status = 'Conditions are good'
    text = f'*{site} conditions report*\n' + conditions_status
    block = {'type': 'section',
             'text': {'text': text, 'type': 'mrkdwn'},
             }
    blocks.append(block)

    # Conditions flags
    text = conditions.get_formatted_string(good=':white_check_mark:',
                                           bad=':exclamation:')
    block = {'type': 'section',
             'text': {'text': text, 'type': 'mrkdwn'},
             }
    blocks.append(block)

    # Conditions timestamp
    ts = conditions.current_time.unix
    text = '<!date^{0}^Last updated {{date_num}} {{time_secs}}|{0}>'.format(int(ts))
    block = {'type': 'context',
             'elements': [{'text': text, 'type': 'mrkdwn'}],
             }
    blocks.append(block)
    # blocks.append({'type': 'divider'})

    # System status
    status = Status()
    if status.mode == 'robotic':
        text = ':robot_face: System is in robotic mode'
    elif status.mode == 'manual':
        text = ':technologist: System is in *manual* mode'
    elif status.mode == 'engineering':
        text = ':mechanic: System is in *engineering* mode'
    block = {'type': 'section',
             'text': {'text': text, 'type': 'mrkdwn'},
             }
    blocks.append(block)
    # blocks.append({'type': 'divider'})
    # Useful links
    if site == 'La Palma':
        env_url = 'http://lapalma-observatory.warwick.ac.uk/environment/'
        mf_url = 'https://www.mountain-forecast.com/peaks/Roque-de-los-Muchachos/forecasts/2423'
        ing_url = 'http://catserver.ing.iac.es/weather/index.php?view=site'
        not_url = 'http://www.not.iac.es/weather/'
        tng_url = 'https://tngweb.tng.iac.es/weather/'
        links = ['<{}|Local environment page>'.format(env_url),
                 '<{}|Mountain forecast>'.format(mf_url),
                 '<{}|ING>'.format(ing_url),
                 '<{}|NOT>'.format(not_url),
                 '<{}|TNG>'.format(tng_url),
                 ]
        text = ' - '.join(links)
        block = {'type': 'section',
                 'text': {'text': text, 'type': 'mrkdwn'},
                 }
        blocks.append(block)

        ext_url = 'http://lapalma-observatory.warwick.ac.uk/eastcam/'
        int_url = 'http://lapalma-observatory.warwick.ac.uk/goto/dome/'
        sat_url = 'https://en.sat24.com/en/ce/infraPolair'
        links = ['<{}|External webcam>'.format(ext_url),
                 '<{}|Internal webcam>'.format(int_url),
                 '<{}|IR satellite>'.format(sat_url),
                 ]
        text = ' - '.join(links)
        block = {'type': 'section',
                 'text': {'text': text, 'type': 'mrkdwn'},
                 }
        blocks.append(block)

        # External webcam
        ts = '{:.0f}'.format(Time.now().unix)
        image_url = 'http://lapalma-observatory.warwick.ac.uk/webcam/ext2/static?' + ts
        text = 'External webcam view'
        # block = {'type': 'image',
        #          'title': {'text': text, 'type': 'plain_text'},
        #          'image_url': image_url,
        #          'alt_text': text,
        #          }
        # blocks.append(block)
        attach = {'text': text,
                  'image_url': image_url,
                  }
        attachments.append(attach)

        # Internal webcam
        image_url = 'http://lapalma-observatory.warwick.ac.uk/webcam/goto/static?' + ts
        text = 'Internal webcam view'
        # block = {'type': 'image',
        #          'title': {'text': text, 'type': 'plain_text'},
        #          'image_url': image_url,
        #          'alt_text': text,
        #          }
        # blocks.append(block)
        attach = {'text': text,
                  'image_url': image_url,
                  }
        attachments.append(attach)

        # IR satellite
        image_url = 'https://en.sat24.com/image?type=infraPolair&region=ce&' + ts
        text = 'IR satellite view'
        # block = {'type': 'image',
        #          'title': {'text': text, 'type': 'plain_text'},
        #          'image_url': image_url,
        #          'alt_text': text,
        #          }
        # blocks.append(block)
        attach = {'text': text,
                  'image_url': image_url,
                  }
        attachments.append(attach)

    send_slack_msg(conditions_status, blocks=blocks, attachments=attachments, channel=slack_channel)


def send_status_report(msg, colour=None, startup=True, slack_channel=None, site=params.SITE_NAME):
    """Send a Slack message with the current conditions, status and webcams."""
    attachments = []

    # Conditions summary
    conditions = Conditions()
    conditions_summary = conditions.get_formatted_string(good=':heavy_check_mark:',
                                                         bad=':exclamation:')
    if conditions.bad:
        conditions_status = ':warning: Conditions are bad! :warning:'
        if colour is None:
            colour = 'danger'
    else:
        conditions_status = 'Conditions are good'
        if colour is None:
            colour = 'good'
    attach = {'fallback': 'Conditions summary',
              'title': conditions_status,
              'text': conditions_summary,
              'color': colour,
              'ts': conditions.current_time.unix,
              }
    attachments.append(attach)

    # System status
    status = Status()
    attach = {'fallback': 'System mode: {}'.format(status.mode),
              'text': 'System is in *{}* mode'.format(status.mode),
              'color': colour,
              }
    attachments.append(attach)

    if startup:
        # Useful links
        if site == 'La Palma':
            env_url = 'http://lapalma-observatory.warwick.ac.uk/environment/'
            mf_url = 'https://www.mountain-forecast.com/peaks/Roque-de-los-Muchachos/forecasts/2423'
            ing_url = 'http://catserver.ing.iac.es/weather/index.php?view=site'
            not_url = 'http://www.not.iac.es/weather/'
            tng_url = 'https://tngweb.tng.iac.es/weather/'
            links = ['<{}|Local environment page>'.format(env_url),
                     '<{}|Mountain forecast>'.format(mf_url),
                     '<{}|ING>'.format(ing_url),
                     '<{}|NOT>'.format(not_url),
                     '<{}|TNG>'.format(tng_url),
                     ]
            attach = {'fallback': 'Useful links',
                      'text': '  -  '.join(links),
                      'color': colour,
                      }
            attachments.append(attach)

        # External webcam
        ts = '{:.0f}'.format(Time.now().unix)
        image_url = 'http://lapalma-observatory.warwick.ac.uk/webcam/ext2/static?' + ts
        attach = {'fallback': 'External webcam view',
                  'title': 'External webcam view',
                  'title_link': 'http://lapalma-observatory.warwick.ac.uk/eastcam/',
                  'text': 'Image attached:',
                  'image_url': image_url,
                  'color': colour,
                  }
        attachments.append(attach)

    if startup:
        # IR satellite
        if site == 'La Palma':
            image_url = 'https://en.sat24.com/image?type=infraPolair&region=ce&' + ts
            attach = {'fallback': 'IR satellite view',
                      'title': 'IR satellite view',
                      'title_link': 'https://en.sat24.com/en/ce/infraPolair',
                      'text': 'Image attached:',
                      'image_url': image_url,
                      'color': colour,
                      }
            attachments.append(attach)
        elif site == 'Siding Spring':
            image_url = 'http://www.bom.gov.au/gms/IDE00005.gif'
            attach = {'fallback': 'IR satellite view',
                      'title': 'IR satellite view',
                      'title_link': 'http://www.bom.gov.au/gms/IDE00005.gif',
                      'text': 'Image attached:',
                      'image_url': image_url,
                      'color': colour,
                      }
            attachments.append(attach)
    else:
        # Internal webcam
        image_url = 'http://lapalma-observatory.warwick.ac.uk/webcam/goto/static?' + ts
        attach = {'fallback': 'Internal webcam view',
                  'title': 'Internal webcam view',
                  'title_link': 'http://lapalma-observatory.warwick.ac.uk/goto/dome/',
                  'text': 'Image attached:',
                  'image_url': image_url,
                  'color': colour,
                  }
        attachments.append(attach)

    send_slack_msg(msg, attachments=attachments, channel=slack_channel)


def send_startup_report(msg, slack_channel=None):
    """Send a Slack message in the evening before observing starts."""
    send_status_report(msg=msg, startup=True, slack_channel=slack_channel)


def send_dome_report(msg, confirmed_closed, slack_channel=None):
    """Send a Slack message in the morning once observing is complete."""
    # Set message colour depending on the dome status
    if confirmed_closed:
        colour = 'good'
    else:
        colour = 'danger'
    send_status_report(msg=msg, colour=colour, startup=False, slack_channel=slack_channel)


def send_timing_report(time=None,
                       startup_sunalt=12,
                       open_sunalt=0,
                       obs_start_sunalt=-12,
                       obs_stop_sunalt=None,
                       close_sunalt=None,
                       slack_channel=None,
                       ):
    """Send a Slack message containing tonight's observing times."""
    if time is None:
        time = Time.now()
    if obs_stop_sunalt is None:
        obs_stop_sunalt = obs_start_sunalt
    if close_sunalt is None:
        close_sunalt = open_sunalt

    startup_time = sunalt_time(startup_sunalt * u.deg, eve=True, time=time)
    open_time = sunalt_time(open_sunalt * u.deg, eve=True, time=time)
    obsstart_time = sunalt_time(obs_start_sunalt * u.deg, eve=True, time=time)
    obsstop_time = sunalt_time(obs_stop_sunalt * u.deg, eve=False, time=time)
    close_time = sunalt_time(close_sunalt * u.deg, eve=False, time=time)
    obs_time = (obsstop_time - obsstart_time).to(u.hour).value

    msg = '*Night starting {}*\n'.format(night_startdate())
    msg += 'Expected observing duration: {:.1f} hours'.format(obs_time)

    attachments = []
    text = ''
    text += startup_time.strftime('%Y-%m-%d %H:%M UTC')
    text += ': Pilot startup (_sunalt={}°_)\n'.format(startup_sunalt)
    text += open_time.strftime('%Y-%m-%d %H:%M UTC')
    text += ': Dome open (_sunalt={}°_)\n'.format(open_sunalt)
    text += obsstart_time.strftime('%Y-%m-%d %H:%M UTC')
    text += ': Observing start (_sunalt={}°_)\n'.format(obs_start_sunalt)
    text += obsstop_time.strftime('%Y-%m-%d %H:%M UTC')
    text += ': Observing finish (_sunalt={}°_)\n'.format(obs_stop_sunalt)
    text += close_time.strftime('%Y-%m-%d %H:%M UTC')
    text += ': Dome closed (_sunalt={}°_)\n'.format(close_sunalt)
    attach = {'fallback': text,
              'text': text,
              }
    attachments.append(attach)

    send_slack_msg(msg, attachments=attachments, channel=slack_channel)
