"""Email messaging tools."""

import smtplib
import time

from . import params


def send_email(recipients=params.EMAIL_LIST, subject='GOTO', message='Test'):
    """Send an email.

    TODO: I'm pretty sure this is broken.
    """
    to_address = ', '.join(recipients)
    from_address = params.EMAIL_ADDRESS
    header = 'To:{}\nFrom:{}\nSubject:{}\n'.format(to_address, from_address, subject)
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())
    text = '{}\n\nMessage sent at {}'.format(message, timestamp)

    server = smtplib.SMTP(params.EMAIL_SERVER)
    server.starttls()
    server.login('goto-observatory@gmail.com', 'password')
    server.sendmail(from_address, recipients, header + '\n' + text + '\n\n')
    server.quit()
    print('Sent mail to', recipients)
