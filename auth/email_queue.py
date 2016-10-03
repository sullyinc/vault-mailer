#!/usr/bin/env python

import datetime
import email.message
import json
import logging
import os
import re
import time
import urllib

import sendgrid
from sendgrid.helpers.mail import Email, Content, Substitution, Mail, Personalization
from tornado import options
from tornado import template
from tornado import escape

from common import sendmail

# try:
#     # Python 3
#     import urllib as urllib
# except ImportError:
# Python 2
import urllib2


options.define('enable_email', False, help='Send notification emails')

_POLL_SLEEP_SECONDS = 5
_DEFAULT_CONTENT_TEXT = 'Please use an email client that displays HTML, such as http://www.gmail.com'

_TYPE_INVITATION = 'user_invite'
_TYPE_SERVICE = 'custom_service'
_TYPE_CREATE = 'create_account'
_TYPE_NEW_USER_INVITE = 'new_user_invitation'
_TYPE_ADDRESS_VERIFICATION = 'address_verification'
_TYPE_LOGIN_ON_NEW_DEVICE = 'new_device_login'
_TYPE_ISSUE_REPORTED = 'issue_reported'
_TYPE_SHARE_NOTIFICATION = 'share_notification';
_VALID_TYPES = set((_TYPE_INVITATION, _TYPE_SERVICE, _TYPE_CREATE, _TYPE_NEW_USER_INVITE,
    _TYPE_ADDRESS_VERIFICATION, _TYPE_LOGIN_ON_NEW_DEVICE))

# Map of db template names to SendGrid template IDs
_TEMPLATE_NAME_TO_TEMPLATE_ID = {
    'onboard-first-secret': '0be812f1-410e-45a3-900c-4d6a78292dc0'
}


def create_sendgrid_client():
    return sendgrid.SendGridAPIClient(apikey=os.environ.get('SENDGRID_API_KEY'))


def make_sendgrid_mail(html_string, subject, to_string, from_string, 
                       text_string=None):
    if text_string is None:
        text_string = _DEFAULT_CONTENT_TEXT
    mail = Mail()

    # SendGrid: minimum required from_email, subject, to_email, and content
    mail.set_from(Email(from_string))
    personalization = Personalization()
    personalization.add_to(Email(to_string))
    mail.add_personalization(personalization)
    mail.set_subject(subject)
     
    content_text = Content("text/plain", text_string)
    content_html = Content("text/html", html_string)
    mail.add_content(content_text)
    mail.add_content(content_html)

    return mail

def send_sendgrid_mail(mail):
    sendgrid_client = create_sendgrid_client()

    try:
        response = sendgrid_client.client.mail.send.post(request_body=mail.get())
    except urllib2.HTTPError as e:
        print e.read()


EMAIL_BLACKLIST = {'sgsprob@arcode.com',
    'felipe.mejia@alolaconnection.com', 'tyronedubose@gmail.com',
    'renee.y.ho@gmail.com'}

def ok_to_send_email(addr):
    if addr in EMAIL_BLACKLIST:
        logging.info('skipping email to %s due to blacklist', addr)
        return False
    else:
        return True

# do-not-reply is typical: Asana, Dropbox (no-reply), and Trello (do-not-reply)
_FROM_ADDR = 'Vault <team@vaultapp.xyz>'
_TEMPLATE_PATH = 'auth/templates'
_TEMPLATE_LOADER = None
_NO_ESCAPING_TEMPLATE_LOADER = None
_EMAILS_TO_SEND_ISSUES_TO = 'team@vaultapp.xyz'

def _generate_template(name, variables, html_escape):
    '''Loads the template from name (with caching) and renders it using variables.
    If html_escape is False, escaping will be disabled.'''
    # create the template loader once; it caches templates
    global _TEMPLATE_LOADER
    global _NO_ESCAPING_TEMPLATE_LOADER
    template_loader = None
    if not html_escape:
        # load templates without autoescaping
        if _NO_ESCAPING_TEMPLATE_LOADER is None:
            _NO_ESCAPING_TEMPLATE_LOADER = template.Loader(_TEMPLATE_PATH, autoescape=None)
        template_loader = _NO_ESCAPING_TEMPLATE_LOADER
    else:
        assert html_escape
        if _TEMPLATE_LOADER is None:
            _TEMPLATE_LOADER = template.Loader(_TEMPLATE_PATH, autoescape='xhtml_escape')
        template_loader = _TEMPLATE_LOADER

    t = template_loader.load(name)
    return t.generate(**variables)


def _drop_template_cache():
    global _TEMPLATE_LOADER
    _TEMPLATE_LOADER = None


def generate_invite_mail(sender_name, sender_email, recipient, service_name, invite_token):
    assert sender_email != recipient

    subject = '%s has given you access to %s' % (sender_name, service_name)

    login_url = 'https://www.vaultapp.xyz/login/google'
    if invite_token is not None:
        login_url += '/' + invite_token

    variables = {
        'sender_name': sender_name,
        'sender_email': sender_email,
        'service_name': service_name,
        'login_url': login_url,
    }

    html = _generate_template('email_invite.html', variables, html_escape=True)
    text = _generate_template('email_invite.txt', variables, html_escape=False)

    mail = make_sendgrid_mail(html, subject, recipient, sender_email, text)
    # Dropbox sets reply-to to the "sharerer"
    mail.set_reply_to(Email(sender_email))
    return mail


_fake_send_count = 0
_last_message = None
def _send(mail):
    """
    Sends a SendGrid mail object.

    Args:
        mail: Sendgrid Mail object.
    """
    if options.options.enable_email:
        send_sendgrid_mail(mail)
    else:
        if (mail.personalizations and mail.personalizations[0].tos):
            logging.info('email disabled; not sending mail to %s', mail.personalizations[0].tos[0])
        else:
            logging.info('email disabled; not sending mail %s', mail)
        global _fake_send_count
        global _last_message
        _fake_send_count += 1
        _last_message = mail


def send_invite(sender_name, sender_email, recipient, service_name, invite_token):
    mail = generate_invite_mail(sender_name, sender_email, recipient, service_name, invite_token)
    _send(mail)


_SERVICE_RECIPIENT = 'team@vaultapp.xyz'
def _send_eng_message(subject, content):
    message = email.message.Message()
    message['Subject'] = subject
    message['From'] = _FROM_ADDR
    message['To'] = _SERVICE_RECIPIENT.encode('us-ascii')
    message['Content-Type'] = 'text/plain; charset=UTF-8'
    message.set_payload(content)
    _send(message)


def send_custom_service(user_email, login_url, service_name, service_id):
    subject = 'Alert: new custom service: %s' % (service_name)

    variables = {
        'user_email': user_email,
        'login_url': login_url,
        'service_name': service_name,
        'service_id': service_id,
    }
    content = _generate_template('email_custom_service.txt', variables, html_escape=False)

    _send_eng_message(subject, content)


def send_create_account(user_email, service_name, instance_id):
    subject = 'Alert: account creation requested: %s' % (service_name)
    content = '''User: %s requested a new account for service: %s

Details:

user: %s
service name: %s
instance id: %d
'''  % (user_email, service_name, user_email, service_name, instance_id)

    _send_eng_message(subject, content)


def generate_new_user_invite(sender_email, recipient_email, temp_password):
    assert sender_email != recipient_email

    subject = '%s has shared an account with you' % (sender_email)

    args = {
        'u': recipient_email,
        'p': temp_password,
    }
    login_url = 'https://www.vaultapp.xyz/install.html' + '#' + \
        urllib.urlencode(args)

    variables = {
        'sender_email': sender_email,
        'recipient_email': recipient_email,
        'temp_password': temp_password,
        'login_url': login_url,
    }

    html = _generate_template('new_user_invite.html', variables, html_escape=True)
    text = _generate_template('new_user_invite.txt', variables, html_escape=False)

    return make_sendgrid_mail(html, subject, recipient_email, _FROM_ADDR, text)


def send_new_user_invite(sender_email, recipient_email, temp_password):
    mail = generate_new_user_invite(sender_email, recipient_email, temp_password)
    # Dropbox sets reply-to to the "sharerer"
    mail.set_reply_to(Email(sender_email))
    _send(mail)


def send_address_verification(recipient_email, verification_code):
    subject = 'Verify your Vault account'

    args = {
        'user': recipient_email,
        'code': verification_code,
    }
    verification_link = 'https://api.vaultapp.xyz/mitro-core/user/VerifyAccount?' + \
        urllib.urlencode(args)

    variables = {
        'verification_link': verification_link
    }
    html = _generate_template('address_verification.html', variables, html_escape=True)
    text = _generate_template('address_verification.txt', variables, html_escape=False)
    mail = make_sendgrid_mail(html, subject, recipient_email, _FROM_ADDR, text)
    if ok_to_send_email(recipient_email):
        _send(mail)


# HTML5 regexp also used in JS
# http://www.whatwg.org/specs/web-apps/current-work/multipage/states-of-the-type-attribute.html#valid-e-mail-address
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$')
def _is_valid_email(email):
    '''Returns True if email passes the HTML5 address regexp.'''

    return _EMAIL_RE.match(email) is not None

def send_issue_reported(user_email_address, url, issue_type, description, issue_id):
    subject = 'Mitro issue report (id: %s url: %s)' % (issue_id, url)
    recipient_email = _EMAILS_TO_SEND_ISSUES_TO
    user_email_address = user_email_address.strip()
    variables = {
        'issue_id': issue_id,
        'user_email': user_email_address,
        'url' : url,
        'issue_type' : issue_type,
        'description' : description
    }

    text = _generate_template('new_issue.txt', variables, html_escape=False)
    mail = make_sendgrid_mail('', subject, recipient_email, _FROM_ADDR, text)
    # The user supplied a "valid" email address: set the reply-to header
    # If we set it for invalid strings, the server rejects the message
    if _is_valid_email(user_email_address) > 0:
        mail.set_reply_to(Email(user_email_address))

    _send(mail)

def send_device_verification(recipient_email, token, token_signature):
    subject = 'Vault: Verify your account for a new device'

    args = {
        'user': recipient_email,
        'token': token,
        'token_signature': token_signature
    }
    verification_link = 'https://api.vaultapp.xyz/mitro-core/user/VerifyDevice?' + \
        urllib.urlencode(args)

    variables = {
        'verification_link': verification_link
    }
    html = _generate_template('device_verification.html', variables, html_escape=True)
    text = _generate_template('device_verification.txt', variables, html_escape=False)
    mail = make_sendgrid_mail(html, subject, recipient_email, _FROM_ADDR, text)

    if ok_to_send_email(recipient_email):
        _send(mail)


def generate_share_notification(sender_name, sender_email, recipient_name, recipient_email, secret_title, secret_url):
    '''Generate an email when a new service is shared with a user.'''
    if not sender_name:
        sender_name = sender_email
    if not recipient_name:
        recipient_name = recipient_email
    if not secret_title:
        secret_title = secret_url

    params = {
        'sender_name': sender_name,
        'sender_email': sender_email,
        'recipient_name': recipient_name,
        'recipient_email': recipient_email,
        'secret_title': secret_title,
        'secret_url': secret_url
    }

    subject = '%s has shared a secret with you' % sender_name

    html = _generate_template('share_notification.html', params, html_escape=True)
    text = _generate_template('share_notification.txt', params, html_escape=False)
    text = ''

    mail = make_sendgrid_mail(html, subject, recipient_email, _FROM_ADDR, text)

    return mail


def send_share_notification(sender_name, sender_email, recipient_name, recipient_email, secret_title, secret_url):
    message = generate_share_notification(sender_name, sender_email, recipient_name, recipient_email, secret_title, secret_url)
    _send(message)


def send_sendgrid_template(template_name, template_params, subject, sender_name, sender_email, recipient_name, recipient_email):
    '''Send a message via the SendGrid API.'''
    if template_name.strip() == 'share-to-recipient-web':
       logging.info('ignoring share to recipient message')
       return

    if template_name not in _TEMPLATE_NAME_TO_TEMPLATE_ID:
        logging.ing('template id not found for template name ' + template_name + ': ignoring message')
        return
    template_id = _TEMPLATE_NAME_TO_TEMPLATE_ID[template_name]

    mail = Mail()

    personalization = Personalization()
    personalization.add_to(Email(recipient_email))
    mail.add_personalization(personalization)

    if sender_email is None:
        sender_email = _FROM_ADDR
    mail.set_from(Email(sender_email))

    if subject:
        mail.set_subject(subject)

    mail.set_template_id(template_id)

    _send(mail)


def send_mandrill_message(template_name, template_params, subject, sender_name, sender_email, recipient_name, recipient_email):
    '''Send a message via the Mandrill API.'''
    message = {'to': [{'email': recipient_email, 'type': 'to'}]}
    if template_name.strip() == 'share-to-recipient-web':
       logging.info('ignoring share to recipient message')
       return

    if recipient_name:
        message['to'][0]['name'] = recipient_name

    # Mandrill templates allow setting defaults for subject, sender_name,
    # and sender_email
    if subject:
        message['subject'] = subject

    if sender_name:
        message['from_name'] = sender_name
    if sender_email:
        message['from_email'] = sender_email

    merge_vars = []
    for key, value in template_params.iteritems():
      merge_vars.append({'name': key, 'content': escape.xhtml_escape(value)});
    message['global_merge_vars'] = merge_vars

    mandrill_client = mandrill.Mandrill(options.options.mandrill_api_key)
    result = mandrill_client.messages.send_template(template_name=template_name,
        template_content=None, message=message, async=False)

    return result


def _queue(session, queue_class, type_string, args,
           template_name, template_params):
    assert type_string in _VALID_TYPES or template_name
    arg_string = json.dumps(args)
    template_params_string = json.dumps(template_params)

    queued_item = queue_class(type_string, arg_string, template_name,
                              template_params_string)
    session.add(queued_item)


def poll_queue(session, queue_class):
    '''Returns a queued item, or None. Commits current transaction.'''

    item = session.query(queue_class).filter_by(attempted_time=None, sent_time=None).first()
    if item is None:
        session.commit()
        return None

    # Mark as attempted; lame attempt to "log" failed items
    assert item.attempted_time is None
    item.attempted_time = datetime.datetime.utcnow()

    # flush then expunge: changes propagate but item can be used without a database session
    session.flush()
    session.expunge(item)
    session.commit()
    return item


def ack_queued_item(session, queue_class, item):
    '''Marks the queued item as processed by updating sent_time. Commits current transaction.'''

    assert session.query(queue_class).filter_by(id=item.id).count() == 1

    # Reload the item as it was expunged earlier
    email_item = session.query(queue_class).filter_by(id=item.id).first()
    email_item.sent_time = datetime.datetime.utcnow()

    session.commit()


_STATSD_POLLS = 'polls'
_STATSD_DEQUEUED = 'dequeued'
_STATSD_SUCCESS = 'success'
_STATSD_FAILED = 'failed'


def _loop_once(session, queue_class, statsd_client):
    item = poll_queue(session, queue_class)
    statsd_client.incr(_STATSD_POLLS)
    if item is None:
        return False

    statsd_client.incr(_STATSD_DEQUEUED)
    arguments = item.get_arguments()
    success = False
    try:
        if item.template_name:
            send_sendgrid_template(item.template_name,
                item.get_template_params(), *arguments)
            logging.info('sent message with sendgrid template %s',
                         item.template_name)
        elif item.type_string == _TYPE_INVITATION:
            send_invite(*arguments)
            logging.info('sent invitation to %s', arguments[2])
        elif item.type_string == _TYPE_SERVICE:
            send_custom_service(*arguments)
            logging.info('sent custom service notification')
        elif item.type_string == _TYPE_CREATE:
            send_create_account(*arguments)
            logging.info('sent create account notification')
        elif item.type_string == _TYPE_NEW_USER_INVITE:
            send_new_user_invite(*arguments)
            logging.info('sent new user invitation: %s', arguments[1])
        elif item.type_string == _TYPE_ADDRESS_VERIFICATION:
            send_address_verification(*arguments)
            logging.info('sent address verification: %s', arguments[0])
        elif item.type_string == _TYPE_LOGIN_ON_NEW_DEVICE:
            send_device_verification(*arguments)
            logging.info('sent device verification: %s', arguments[0])
        elif item.type_string == _TYPE_ISSUE_REPORTED:
            send_issue_reported(*arguments)
            logging.info('sent new issue: %s', arguments[-1])
        elif item.type_string == _TYPE_SHARE_NOTIFICATION:
            send_share_notification(*arguments)
            logging.info('sent share notification')
        else:
            raise Exception('Unsupported type: ' + item.type_string)

        ack_queued_item(session, queue_class, item)
        success = True
        statsd_client.incr(_STATSD_SUCCESS)
    except:
        logging.exception('Exception caught for type %s args %s',
            item.type_string, repr(arguments))
        success = False
        statsd_client.incr(_STATSD_FAILED)

    return success


class NullStatsd(object):
    def incr(self, bucket):
        pass


def poll_forever(session_constructor, queue_class, statsd_client=NullStatsd()):
    '''Polls for emails, sending them if needed.'''

    while True:
        session = session_constructor()
        processed_item = _loop_once(session, queue_class, statsd_client)
        session.close()

        if not processed_item:
            time.sleep(_POLL_SLEEP_SECONDS)


class BackoffSleeper(object):
    '''Sleeps for an exponential amount of time. Returns True if it slept,
    False if it shouldn't retry.'''

    # If the last sleep ended this long ago, we assume we didn't fail and reset the count
    _RESET_SECONDS = 60

    # Sequence of times to sleep before retrying. After we run out, the sleeper returns False
    # Total time = 2 minutes
    _SLEEP_SECONDS = [5, 10, 15, 30, 60]

    def __init__(self, time_module=time):
        self.time_module = time_module
        self.count = 0
        self.last_sleep_end = self.time_module.time()

    def shouldRetryAfterSleep(self):
        now = self.time_module.time()
        diff = now - self.last_sleep_end

        if diff >= BackoffSleeper._RESET_SECONDS:
            # We slept a long time: reset the count
            self.count = 0

        if self.count >= len(BackoffSleeper._SLEEP_SECONDS):
            return False

        sleep_time = BackoffSleeper._SLEEP_SECONDS[self.count]
        self.time_module.sleep(sleep_time)
        self.count += 1
        self.last_sleep_end = self.time_module.time()
        return True

    def max_retries(self):
        return len(BackoffSleeper._SLEEP_SECONDS)
