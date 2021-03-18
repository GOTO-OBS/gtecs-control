"""Slack messaging tools."""

import datetime
import os
import time
from collections import Counter

import astropy.units as u
from astropy.time import Time

import slack

import obsdb as db

from . import params
from .astronomy import observatory_location
from .flags import Conditions, Status


def send_slack_msg(text, attachments=None, filepath=None, channel=params.SLACK_BOT_CHANNEL):
    """Send a message to Slack, using the settings defined in `gtecs.params`.

    Parameters
    ----------
    text : string
        The message text.

    attachments : dict, optional
        Attachments to the message.
        NB a message can have attachments OR a file, not both.

    filepath : string, optional
        A local path to a file to be added to the message.
        NB a message can have a file OR attachments, not both.

    channel : string, optional
        The channel to post the message to.
        Defaults to `gtecs.params.SLACK_BOT_CHANNEL`.

    """
    text = str(text)
    if attachments is not None and filepath is not None:
        raise ValueError("A Slack message can't have both attachments and a file.")

    if params.ENABLE_SLACK:
        client = slack.WebClient(params.SLACK_BOT_TOKEN)
        try:
            if not filepath:
                api_call = client.chat_postMessage(channel=channel,
                                                   username=params.SLACK_BOT_NAME,
                                                   as_user=True,
                                                   text=text,
                                                   attachments=attachments,
                                                   )
            else:
                filename = os.path.basename(filepath)
                name = os.path.splitext(filename)[0]
                with open(filepath, 'rb') as file:
                    api_call = client.files_upload(channels=channel,  # Note channel(s)
                                                   username=params.SLACK_BOT_NAME,
                                                   as_user=True,
                                                   initial_comment=text,
                                                   filename=filename,
                                                   file=file,
                                                   title=name,
                                                   )
            if not api_call.get('ok'):
                raise Exception('Unable to send message')
        except Exception as err:
            print('Connection to Slack failed! - {}'.format(err))
            print('Message:', text)
            print('Attachments:', attachments)
            print('Filepath:', filepath)
    else:
        print('Slack Message:', text)
        print('Attachments:', attachments)
        print('Filepath:', filepath)


def send_startup_report():
    """Send a Slack message with a summery of the current conditions and pending pointings."""
    msg = '*Pilot reports startup complete*'
    conditions = Conditions()
    conditions_summary = conditions.get_formatted_string(good=':heavy_check_mark:',
                                                         bad=':exclamation:')
    if conditions.bad:
        msg2 = ':warning: Conditions are bad! :warning:'
        colour = 'danger'
    else:
        msg2 = 'Conditions are good'
        colour = 'good'
    attach_conds = {'fallback': 'Conditions summary',
                    'title': msg2,
                    'text': conditions_summary,
                    'color': colour,
                    'ts': conditions.current_time.unix,
                    }

    status = Status()
    attach_status = {'fallback': 'System mode: {}'.format(status.mode),
                     'text': 'System is in *{}* mode'.format(status.mode),
                     'color': colour,
                     }

    env_url = 'http://lapalma-observatory.warwick.ac.uk/environment/'
    mf_url = 'https://www.mountain-forecast.com/peaks/Roque-de-los-Muchachos/forecasts/2423'
    ing_url = 'http://catserver.ing.iac.es/weather/index.php?view=site'
    not_url = 'http://www.not.iac.es/weather/'
    tng_url = 'https://tngweb.tng.iac.es/weather/'
    links = ['<{}|Local enviroment page>'.format(env_url),
             '<{}|Mountain forecast>'.format(mf_url),
             '<{}|ING>'.format(ing_url),
             '<{}|NOT>'.format(not_url),
             '<{}|TNG>'.format(tng_url),
             ]
    attach_links = {'fallback': 'Useful links',
                    'text': '  -  '.join(links),
                    'color': colour,
                    }

    ts = '{:.0f}'.format(conditions.current_time.unix)
    webcam_url = 'http://lapalma-observatory.warwick.ac.uk/webcam/ext2/static?' + ts
    attach_webcm = {'fallback': 'External webcam view',
                    'title': 'External webcam view',
                    'title_link': 'http://lapalma-observatory.warwick.ac.uk/eastcam/',
                    'text': 'Image attached:',
                    'image_url': webcam_url,
                    'color': colour,
                    }

    sat_url = 'https://en.sat24.com/image?type=infraPolair&region=ce&' + ts
    attach_irsat = {'fallback': 'IR satellite view',
                    'title': 'IR satellite view',
                    'title_link': 'https://en.sat24.com/en/ce/infraPolair',
                    'text': 'Image attached:',
                    'image_url': sat_url,
                    'color': colour,
                    }

    attachments = [attach_conds, attach_status, attach_links, attach_webcm, attach_irsat]
    send_slack_msg(msg, attachments=attachments)


def send_dome_report(msg, confirmed_closed):
    """Send Slack message with webcams attached."""
    if confirmed_closed:
        colour = 'good'
    else:
        colour = 'danger'

    conditions = Conditions()
    conditions_summary = conditions.get_formatted_string(good=':heavy_check_mark:',
                                                         bad=':exclamation:')
    if conditions.bad:
        msg2 = ':warning: Conditions are bad! :warning:'
    else:
        msg2 = 'Conditions are good'
    attach_conds = {'fallback': 'Conditions summary',
                    'title': msg2,
                    'text': conditions_summary,
                    'color': colour,
                    'ts': conditions.current_time.unix,
                    }

    status = Status()
    attach_status = {'fallback': 'System mode: {}'.format(status.mode),
                     'text': 'System is in *{}* mode'.format(status.mode),
                     'color': colour,
                     }

    ts = '{:.0f}'.format(time.time())
    ext_url = 'http://lapalma-observatory.warwick.ac.uk/webcam/ext2/static?' + ts
    attach_ext = {'fallback': 'External webcam view',
                  'title': 'External webcam view',
                  'title_link': 'http://lapalma-observatory.warwick.ac.uk/eastcam/',
                  'text': 'Image attached:',
                  'image_url': ext_url,
                  'color': colour,
                  }
    int_url = 'http://lapalma-observatory.warwick.ac.uk/webcam/goto/static?' + ts
    attach_int = {'fallback': 'Internal webcam view',
                  'title': 'Internal webcam view',
                  'title_link': 'http://lapalma-observatory.warwick.ac.uk/goto/dome/',
                  'text': 'Image attached:',
                  'image_url': int_url,
                  'color': colour,
                  }

    attachments = [attach_conds, attach_status, attach_ext, attach_int]
    send_slack_msg(msg, attachments=attachments)


def send_database_report():
    """Send a Slack message containing the pending pointings in the database."""
    # Report on pending pointings
    with db.open_session() as session:
        pointings = session.query(db.Pointing).filter(db.Pointing.status == 'pending').all()
        msg = '*There are {} pending pointings in the database*\n'.format(len(pointings))

        # Pending pointings that are associated with a non-event survey
        surveys = [pointing.survey for pointing in pointings
                   if pointing.survey is not None and pointing.survey.event_id is None]
        if len(surveys) > 0:
            # Print number of pointings and surveys
            survey_counter = Counter(surveys)
            msg += '{} pointing{} from {} sky survey{}:\n'.format(
                len(surveys), 's' if len(surveys) != 1 else '',
                len(survey_counter), 's' if len(survey_counter) != 1 else '')
            # Print info for all surveys
            for survey, count in survey_counter.most_common():
                msg += '- `{}`'.format(survey.name)
                msg += ' (_'
                msg += '{} pointing{}'.format(count, 's' if count != 1 else '')
                survey_pointings = [pointing for pointing in pointings
                                    if pointing.survey == survey]
                ranks = sorted(set([p.rank for p in survey_pointings]))
                msg += ', rank={}{}'.format(ranks[0], '+' if len(ranks) > 1 else '')
                msg += '_)\n'
        else:
            msg += '0 pointings from sky surveys\n'

        # Pending pointings that are associated with an event survey
        surveys = [pointing.survey for pointing in pointings
                   if pointing.survey is not None and pointing.survey.event_id is not None]
        if len(surveys) > 0:
            # Print number of pointings and surveys
            survey_counter = Counter(surveys)
            msg += '{} pointing{} from {} event follow-up survey{}:\n'.format(
                len(surveys), 's' if len(surveys) != 1 else '',
                len(survey_counter), 's' if len(survey_counter) != 1 else '')
            # Print info for all surveys
            for survey, count in survey_counter.most_common():
                msg += '- `{}`'.format(survey.name)
                msg += ' (_'
                msg += '{} pointing{}'.format(count, 's' if count != 1 else '')
                survey_pointings = [pointing for pointing in pointings
                                    if pointing.survey == survey]
                ranks = sorted(set([p.rank for p in survey_pointings]))
                msg += ', rank={}{}'.format(ranks[0], '+' if len(ranks) > 1 else '')
                msg += '_)'
                start_time = survey.mpointings[0].start_time
                if start_time is not None:
                    start_time = Time(start_time, format='datetime')
                    event_age = (Time.now() - start_time)
                    msg += ' - {:.1f} hours since event\n'.format(event_age.to(u.hour).value)
        else:
            msg += '0 pointings from event follow-up surveys\n'

        # Remaining pending pointings
        objects = [pointing.object_name for pointing in pointings
                   if pointing.survey is None]
        if len(objects) > 0:
            # Print number of pointings and objects
            objects_counter = Counter(objects)
            msg += '{} non-survey pointing{} of {} object{}:\n'.format(
                len(objects), 's' if len(objects) != 1 else '',
                len(objects_counter), 's' if len(objects_counter) != 1 else '')
            # Print info for all objects
            for object, count in objects_counter.most_common():
                msg += '- `{}`'.format(object)
                msg += ' (_'
                msg += '{} pointing{}'.format(count, 's' if count != 1 else '')
                object_pointings = [pointing for pointing in pointings
                                    if pointing.object_name == object]
                ranks = sorted(set([p.rank for p in object_pointings]))
                msg += ', rank={}{}'.format(ranks[0], '+' if len(ranks) > 1 else '')
                msg += '_)\n'
        else:
            msg += '0 non-survey pointings\n'

    send_slack_msg(msg)


def send_observation_report(date, alt_limit=30, sun_limit=-12):
    """Send Slack message with observation plots attached."""
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
        title = 'GOTO observations for\nnight beginning {}'.format(date)
        filepath = os.path.join(plot_direc, '{}_observed.png'.format(date))
        grid.plot(filename=filepath,
                  color={tilename: '0.5' for tilename in notvisible_tiles},
                  highlight=all_tiles,
                  highlight_label=all_surveys,
                  alpha=0.5,
                  title=title)

        # Send message to Slack with the plot attached
        send_slack_msg('Last night coverage plot', filepath=filepath)

        # Create plot of all-sky survey coverage (assuming we observed any new ones)
        if n_obs_allsky > 0:
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
            send_slack_msg('All-sky survey coverage plot', filepath=filepath)

    return n_obs, n_obs_allsky
