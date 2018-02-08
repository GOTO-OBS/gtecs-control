"""
Slack messaging tools
"""

import os

from slackclient import SlackClient

from astropy.time import Time
from astropy.utils.decorators import lazyproperty

from . import params


READ_WEBSOCKET_DELAY = 1
BOT_TOKEN = params.SLACK_BOT_TOKEN
BOT_NAME = params.SLACK_BOT_NAME
CHANNEL_NAME = params.SLACK_BOT_CHANNEL


def send_slack_msg(msg):
    if params.ENABLE_SLACK:
        bot = SlackBot()
        bot.send_message(msg)
    else:
        print('SLACK:', msg)


class SlackBot:
    def __init__(self):
        self.name = BOT_NAME
        self.token = BOT_TOKEN
        self.client = SlackClient(BOT_TOKEN)

    def get_users(self):
        api_call = self.client.api_call("users.list")
        if api_call.get('ok'):
            users = api_call.get('members')
            return ((user.get('name'), user.get('id')) for user in users)
        else:
            raise Exception('cannot obtain user list')

    @lazyproperty
    def id(self):
        for u, i in self.get_users():
            if u == BOT_NAME:
                return i

    @lazyproperty
    def atbot(self):
        return "<@" + self.id + ">"

    @lazyproperty
    def channel(self):
        api_call = self.client.api_call("channels.list")
        if api_call.get('ok'):
            channel = [channel for channel in api_call.get('channels')
                       if channel.get('name') == "lapalma"][0]
            return channel['id']
        else:
            raise Exception("cannot get channel")

    def send_message(self, msg):
        """
        Send a message to the channel for this bot
        """
        api_call = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="@channel " + msg,
            username=self.name
        )
        if not api_call.get('ok'):
            raise Exception('unable to post message')
