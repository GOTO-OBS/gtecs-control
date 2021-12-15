"""Slack messaging tools."""

import datetime
import os
from collections import Counter

import astropy.units as u
from astropy.time import Time

from gtecs.common.slack import send_message
from gtecs.obs import database as db

from . import params
from .astronomy import night_startdate, observatory_location, sunalt_time
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


def send_conditions_report(slack_channel=None):
    """Send a Slack message with the current conditions, status and webcams."""
    blocks = []
    attachments = []

    # Conditions summary
    conditions = Conditions()
    if conditions.bad:
        conditions_status = ':warning: Conditions are bad! :warning:'
    else:
        conditions_status = 'Conditions are good'
    text = '*La Palma conditions report*\n' + conditions_status
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


def send_status_report(msg, colour=None, startup=True, slack_channel=None):
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
        image_url = 'https://en.sat24.com/image?type=infraPolair&region=ce&' + ts
        attach = {'fallback': 'IR satellite view',
                  'title': 'IR satellite view',
                  'title_link': 'https://en.sat24.com/en/ce/infraPolair',
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


def send_timing_report(date=None,
                       startup_sunalt=12,
                       open_sunalt=0,
                       obs_start_sunalt=-12,
                       obs_stop_sunalt=None,
                       close_sunalt=None,
                       slack_channel=None,
                       ):
    """Send a Slack message containing tonight's observing times."""
    if date is None:
        date = night_startdate()
    if obs_stop_sunalt is None:
        obs_stop_sunalt = obs_start_sunalt
    if close_sunalt is None:
        close_sunalt = open_sunalt

    startup_time = sunalt_time(date, startup_sunalt * u.deg, eve=True)
    open_time = sunalt_time(date, open_sunalt * u.deg, eve=True)
    obsstart_time = sunalt_time(date, obs_start_sunalt * u.deg, eve=True)
    obsstop_time = sunalt_time(date, obs_stop_sunalt * u.deg, eve=False)
    close_time = sunalt_time(date, close_sunalt * u.deg, eve=False)
    obs_time = (obsstop_time - obsstart_time).to(u.hour).value

    msg = '*Night starting {}*\n'.format(date)
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


def send_database_report(slack_channel=None):
    """Send a Slack message containing the pending pointings in the database."""
    attachments = []
    with db.open_session() as session:
        pointings = session.query(db.Pointing).filter(db.Pointing.status == 'pending').all()
        msg = '*There are {} pending pointings in the database*'.format(len(pointings))

        # Pending pointings that are associated with a non-event survey
        surveys = [pointing.survey for pointing in pointings
                   if pointing.survey is not None and pointing.survey.event_id is None]
        if len(surveys) > 0:
            # Print number of pointings and surveys
            survey_counter = Counter(surveys)
            title = '{} pointing{} from {} sky survey{}:'.format(
                len(surveys), 's' if len(surveys) != 1 else '',
                len(survey_counter), 's' if len(survey_counter) != 1 else '')
            # Print info for all surveys
            text = '\n'
            for survey, count in survey_counter.most_common():
                text += '- `{}`'.format(survey.name)
                text += ' (_'
                text += '{} pointing{}'.format(count, 's' if count != 1 else '')
                survey_pointings = [pointing for pointing in pointings
                                    if pointing.survey == survey]
                ranks = sorted(set([p.rank for p in survey_pointings]))
                text += ', rank={}{}'.format(ranks[0], '+' if len(ranks) > 1 else '')
                text += '_)\n'
        else:
            title = '0 pointings from sky surveys'
            text = ''
        attach = {'fallback': title,
                  'text': title + text,
                  }
        attachments.append(attach)

        # Pending pointings that are associated with an event survey
        surveys = [pointing.survey for pointing in pointings
                   if pointing.survey is not None and pointing.survey.event_id is not None]
        if len(surveys) > 0:
            # Print number of pointings and surveys
            survey_counter = Counter(surveys)
            title = '{} pointing{} from {} event follow-up survey{}:'.format(
                len(surveys), 's' if len(surveys) != 1 else '',
                len(survey_counter), 's' if len(survey_counter) != 1 else '')
            # Print info for all surveys
            text = '\n'
            for survey, count in survey_counter.most_common():
                text += '- `{}`'.format(survey.name)
                text += ' (_'
                text += '{} pointing{}'.format(count, 's' if count != 1 else '')
                survey_pointings = [pointing for pointing in pointings
                                    if pointing.survey == survey]
                ranks = sorted(set([p.rank for p in survey_pointings]))
                text += ', rank={}{}'.format(ranks[0], '+' if len(ranks) > 1 else '')
                text += '_)'
                start_time = survey.mpointings[0].start_time
                if start_time is not None:
                    start_time = Time(start_time, format='datetime')
                    event_age = (Time.now() - start_time)
                    text += ' - {:.1f} hours since detection'.format(event_age.to(u.hour).value)
                text += '\n'
        else:
            title = '0 pointings from event follow-up surveys'
            text = ''
        attach = {'fallback': title,
                  'text': title + text,
                  }
        attachments.append(attach)

        # Remaining pending pointings
        objects = [pointing.object_name for pointing in pointings
                   if pointing.survey is None]
        if len(objects) > 0:
            # Print number of pointings and objects
            objects_counter = Counter(objects)
            title = '{} non-survey pointing{} of {} object{}:'.format(
                len(objects), 's' if len(objects) != 1 else '',
                len(objects_counter), 's' if len(objects_counter) != 1 else '')
            # Print info for all objects
            text = '\n'
            for object, count in objects_counter.most_common():
                text += '- `{}`'.format(object)
                text += ' (_'
                text += '{} pointing{}'.format(count, 's' if count != 1 else '')
                object_pointings = [pointing for pointing in pointings
                                    if pointing.object_name == object]
                ranks = sorted(set([p.rank for p in object_pointings]))
                text += ', rank={}{}'.format(ranks[0], '+' if len(ranks) > 1 else '')
                text += '_)\n'
        else:
            title += '0 non-survey pointings'
            text = ''
        attach = {'fallback': title,
                  'text': title + text,
                  }
        attachments.append(attach)

    send_slack_msg(msg, attachments=attachments, channel=slack_channel)


def send_observation_report(date=None, alt_limit=30, sun_limit=-12, slack_channel=None):
    """Send a Slack message containing last night's observation plots."""
    if date is None:
        date = night_startdate()

    plot_direc = os.path.join(params.FILE_PATH, 'plots')
    if not os.path.exists(plot_direc):
        os.mkdir(plot_direc)

    # Get the dates for the start and end of the night just finished
    midday_yesterday = datetime.datetime.strptime(date + ' 12:00:00', '%Y-%m-%d %H:%M:%S')
    midday_today = midday_yesterday + datetime.timedelta(days=1)

    with db.open_session() as session:
        # Get the current grid from the database and create a SkyGrid
        db_grid = db.get_current_grid(session)
        grid = db_grid.get_skygrid()

        # Use Astroplan to get all the tiles that would have been visible last night
        visible_tiles = grid.get_visible_tiles(observatory_location(),
                                               time_range=(Time(midday_yesterday),
                                                           Time(midday_today)),
                                               alt_limit=alt_limit,
                                               sun_limit=sun_limit,
                                               )
        notvisible_tiles = [tile for tile in grid.tilenames if tile not in visible_tiles]

        # Get all (on-grid) pointings observed last night
        pointings = session.query(db.Pointing).filter(
            db.Pointing.status == 'completed',
            db.Pointing.grid == db_grid,
            db.Pointing.stopped_time > midday_yesterday,
            db.Pointing.stopped_time < midday_today,
        ).all()

        all_surveys = []
        all_tiles = []

        # First find the all-sky survey tiles completed last night
        allsky_survey = db_grid.surveys[0]
        tiles = [pointing.grid_tile.name for pointing in pointings
                 if pointing.survey == allsky_survey]
        all_surveys.append(allsky_survey.name)
        all_tiles.append(tiles)

        # Then find the tiles of any other survey pointings completed last night
        surveys = [pointing.survey for pointing in pointings
                   if pointing.survey != allsky_survey and pointing.survey is not None]
        if len(surveys) > 0:
            survey_counter = Counter(surveys)
            for survey, _ in survey_counter.most_common():
                tiles = [pointing.grid_tile.name for pointing in pointings
                         if pointing.survey == survey]
                all_surveys.append(survey.name)
                all_tiles.append(tiles)

        # Get the object names for other non-survey pointings
        # Here we count them as small, single-tile surveys
        objects = [pointing.object_name for pointing in pointings
                   if pointing.survey is None]
        if len(objects) > 0:
            object_counter = Counter(objects)
            for object, _ in object_counter.most_common():
                tiles = [pointing.grid_tile.name for pointing in pointings
                         if pointing.object_name == object]
                all_surveys.append(object)
                all_tiles.append(tiles)

    # Remove the empty all-sky survey list if we didn't observe any
    n_obs = sum(len(tiles) for tiles in all_tiles)
    n_obs_allsky = len(all_tiles[0])
    if n_obs_allsky == 0:
        all_surveys = all_surveys[1:]
        all_tiles = all_tiles[1:]

    # Make a plot of last night's observations (assuming we observed anything)
    if n_obs > 0:
        msg = 'Last night coverage plot'

        # Create plot
        title = 'GOTO observations for\nnight beginning {}'.format(date)
        filepath = os.path.join(plot_direc, '{}_observed.png'.format(date))
        grid.plot(filename=filepath,
                  color={tilename: '0.5' for tilename in notvisible_tiles},
                  highlight=all_tiles,
                  highlight_label=all_surveys,
                  alpha=0.5,
                  title=title)

        # Send message to Slack with the plot attached
        send_slack_msg(msg, filepath=filepath, channel=slack_channel)

        # Create plot of all-sky survey coverage (assuming we observed any new ones)
        if n_obs_allsky > 0:
            msg = 'All-sky survey coverage plot'

            with db.open_session() as session:
                # Get the current survey from the database
                db_grid = db.get_current_grid(session)
                db_survey = db_grid.surveys[0]

                # Get all completed all-sky survey pointings since it started
                query = session.query(db.Pointing).filter(
                    db.Pointing.status == 'completed',
                    db.Pointing.survey == db_survey,
                    db.Pointing.stopped_time < midday_today,
                )
                survey_pointings = query.all()

                # Count tiles
                counter = Counter([p.grid_tile.name for p in survey_pointings])
                count_dict = dict(counter)

                # Get start date of the survey
                startdate = min(p.stopped_time for p in survey_pointings)

            # Create plot
            title = 'GOTO all-sky survey coverage\n'
            title += 'from {} to {}'.format(startdate.strftime('%Y-%m-%d'), date)
            filepath = os.path.join(plot_direc, '{}_survey.png'.format(date))
            grid.plot(filename=filepath,
                      color=count_dict,
                      discrete_colorbar=True,
                      highlight=all_tiles[0],
                      highlight_color='red',
                      highlight_label='observed last night',
                      alpha=0.5,
                      title=title)

            # Send message to Slack with the plot attached
            send_slack_msg(msg, filepath=filepath, channel=slack_channel)
        else:
            send_slack_msg('No all-sky survey tiles were observed last night',
                           channel=slack_channel)
    else:
        send_slack_msg('No tiles were observed last night', channel=slack_channel)

    return n_obs, n_obs_allsky
