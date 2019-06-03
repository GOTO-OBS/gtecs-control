"""Slack messaging tools."""

import os

from slackclient import SlackClient

from . import params

BOT_TOKEN = params.SLACK_BOT_TOKEN
BOT_NAME = params.SLACK_BOT_NAME
CHANNEL_NAME = params.SLACK_BOT_CHANNEL


def send_slack_msg(text, attachments=None, filepath=None):
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

    """
    if attachments is not None and filepath is not None:
        raise ValueError("A Slack message can't have both attachments and a file.")

    if params.ENABLE_SLACK:
        client = SlackClient(BOT_TOKEN)
        try:
            if not filepath:
                api_call = client.api_call('chat.postMessage',
                                           channel=CHANNEL_NAME,
                                           username=BOT_NAME,
                                           as_user=True,
                                           text=text,
                                           attachments=attachments,
                                           )
            else:
                filename = os.path.basename(filepath)
                name = os.path.splitext(filename)[0]
                with open(filepath, 'rb') as file:
                    api_call = client.api_call('files.upload',
                                               channels=CHANNEL_NAME,  # Note channel(s)
                                               username=BOT_NAME,
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
