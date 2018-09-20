"""Slack messaging tools."""

from astropy.utils.decorators import lazyproperty

from slackclient import SlackClient

from . import params


READ_WEBSOCKET_DELAY = 1
BOT_TOKEN = params.SLACK_BOT_TOKEN
BOT_NAME = params.SLACK_BOT_NAME
CHANNEL_NAME = params.SLACK_BOT_CHANNEL


def send_slack_msg(msg, attachments=None):
    """Send a Slack message to the GOTO channel."""
    if params.ENABLE_SLACK:
        bot = SlackBot()
        try:
            bot.send_message(msg, attachments)
        except Exception:
            print('Connection to Slack failed!')
            if not attachments:
                print('SLACK:', msg)
            else:
                print('SLACK:', msg, attachments)
    else:
        if not attachments:
            print('SLACK:', msg)
        else:
            print('SLACK:', msg, attachments)


class SlackBot(object):
    """A Slack Bot to send messages."""

    def __init__(self):
        self.name = BOT_NAME
        self.token = BOT_TOKEN
        self.client = SlackClient(BOT_TOKEN)

    def get_users(self):
        """Get the Slack users."""
        api_call = self.client.api_call("users.list")
        if api_call.get('ok'):
            users = api_call.get('members')
            return ((user.get('name'), user.get('id')) for user in users)
        else:
            raise Exception('cannot obtain user list')

    @lazyproperty
    def slack_id(self):
        """Get the ID of a user."""
        for u, i in self.get_users():
            if u == BOT_NAME:
                return i

    @lazyproperty
    def atbot(self):
        """Get a @ reference to this bot."""
        return "<@" + self.slack_id + ">"

    @lazyproperty
    def channel(self):
        """Get the channel to send messages to."""
        api_call = self.client.api_call("channels.list")
        if api_call.get('ok'):
            channel = [channel for channel in api_call.get('channels')
                       if channel.get('name') == "lapalma"][0]
            return channel['id']
        else:
            raise Exception("cannot get channel")

    def send_message(self, msg, attachments=None):
        """Send a message to the channel for this bot."""
        text = "@channel " + msg
        if not attachments:
            attachments = {}
        api_call = self.client.api_call("chat.postMessage",
                                        channel=self.channel, username=self.name,
                                        text=text, attachments=attachments)
        if not api_call.get('ok'):
            raise Exception('unable to post message')
