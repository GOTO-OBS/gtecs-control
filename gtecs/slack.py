"""Slack messaging tools."""

import os

import slack

from . import params


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
