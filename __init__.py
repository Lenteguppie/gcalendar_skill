from adapt.intent import IntentBuilder
from mycroft import MycroftSkill, intent_file_handler
from mycroft.messagebus.message import Message
from mycroft.util.log import LOG

import httplib2
from googleapiclient import discovery, channel

from os.path import dirname, join

import sys
from tzlocal import get_localzone
import pytz
from datetime import datetime, timedelta
from mycroft.util.parse import extract_datetime
from mycroft.util import play_wav
from mycroft.util import time as m_time

import uuid

from requests import HTTPError

from .mycroft_token_cred import MycroftTokenCredentials
from .local_save import LocalSave

REMINDER_PING = join(dirname(__file__), 'twoBeep.wav')

# reminder_chanel = new_webhook_channel("https://zod.aquariumnetwork.com/calendar")

UTC_TZ = u'+00:00'

events = []

event_reminders = {}
handled_reminders = {}

MINUTES = 60  # seconds

def nice_time(dt, lang="en-us", speech=True, use_24hour=False,
              use_ampm=False):
    """
    Format a time to a comfortable human format

    For example, generate 'five thirty' for speech or '5:30' for
    text display.

    Args:
        dt (datetime): date to format (assumes already in local timezone)
        lang (str): code for the language to use
        speech (bool): format for speech (default/True) or display (False)=Fal
        use_24hour (bool): output in 24-hour/military or 12-hour format
        use_ampm (bool): include the am/pm for 12-hour format
    Returns:
        (str): The formatted time string
    """

    if use_24hour:
        # e.g. "03:01" or "14:22"
        string = dt.strftime("%H:%M")
    else:
        if use_ampm:
            # e.g. "3:01 AM" or "2:22 PM"
            string = dt.strftime("%I:%M %p")
        else:
            # e.g. "3:01" or "2:22"
            string = dt.strftime("%I:%M")
        if string[0] == '0':
            string = string[1:]  # strip leading zeros
        return string

    if not speech:
        return string

    # Generate a speakable version of the time
    if use_24hour:
        speak = ""

        # Either "0 8 hundred" or "13 hundred"
        if string[0] == '0':
            if string[1] == '0':
                speak = "0 0"
            else:
                speak = "0 " + string[1]
        else:
            speak += string[0:2]

        if string[3] == '0':
            if string[4] == '0':
                # Ignore the 00 in, for example, 13:00
                speak += " oclock"  # TODO: Localize
            else:
                speak += " o " + string[4]  # TODO: Localize
        else:
            if string[0] == '0':
                speak += " " + string[3:5]
            else:
                # TODO: convert "23" to "twenty three" in helper method

                # Mimic is speaking "23 34" as "two three 43" :(
                # but it does say "2343" correctly.  Not ideal for general
                # TTS but works for the moment.
                speak += ":" + string[3:5]

        return speak
    else:
        if lang.startswith("en"):
            if dt.hour == 0 and dt.minute == 0:
                return "midnight"  # TODO: localize
            if dt.hour == 12 and dt.minute == 0:
                return "noon"  # TODO: localize
            # TODO: "half past 3", "a quarter of 4" and other idiomatic times

            # lazy for now, let TTS handle speaking "03:22 PM" and such
        return string

def remove_duplicates_list(x):
  return list(dict.fromkeys(x))

def to_local_tz(d):
    return m_time.to_local(d)

def is_today(d):
    return d.date() == datetime.today().date()


def is_tomorrow(d):
    return d.date() == datetime.today().date() + timedelta(days=1)


def is_wholeday_event(e):
    return 'dateTime' not in e['start']

def remove_tz(string):
    return string[:-6]

## TESTING callback receiver: 
def construct_watch(address:str):
    eventcollect = {
        'id': str(uuid.uuid1()),
        'type': "web_hook",
        'address': address
    }
    return eventcollect

class GoogleCalendarSkill(MycroftSkill):
    def __init__(self):
        super(GoogleCalendarSkill, self).__init__('Google Calendar')

    @property
    def use_24hour(self):
        return self.config_core.get('time_format') == 'full'

    def __calendar_connect(self, msg=None):
        argv = sys.argv
        sys.argv = []
        try:
            # Get token for this skill (id 4)
            self.credentials = MycroftTokenCredentials(4)
            LOG.info('Credentials: {}'.format(self.credentials))
            http = self.credentials.authorize(httplib2.Http())
            self.service = discovery.build('calendar', 'v3', http=http)
            sys.argv = argv
            self.register_intents()
            
            self.cancel_scheduled_event('calendar_connect')
            self.sync_event_reminders()

            #create a watch for google calendar push notifications
            self.watch = construct_watch("https://yeplab.com:6455/googlecalendar")
            self.watch_uuid= self.watch['id']
            self.add_watch(self.watch)
        except HTTPError:
            LOG.info('No Credentials available')
            pass
    
    def add_watch(self, watch_body):
        self.service.events().watch(calendarId='primary', body=watch_body).execute()

    def register_intents(self):
        intent = IntentBuilder('GetNextAppointmentIntent')\
            .require('NextKeyword')\
            .one_of('AppointmentKeyword', 'ScheduleKeyword')\
            .build()
        self.register_intent(intent, self.get_next)
        
        intent = IntentBuilder('GetNextAppointmentIntent')\
            .require('NextKeyword')\
            .one_of('AppointmentKeyword', 'ScheduleKeyword')\
            .build()
        self.register_intent(intent, self.get_next)

        

        intent = IntentBuilder('GetTodayAppointmentIntent')\
            .require('TodayKeyword')\
            .one_of('AppointmentKeyword', 'ScheduleKeyword')\
            .build()
        self.register_intent(intent, self.get_event_today)

        intent = IntentBuilder('GetDaysAppointmentsIntent')\
            .require('QueryKeyword')\
            .one_of('AppointmentKeyword', 'ScheduleKeyword')\
            .build()
        self.register_intent(intent, self.get_day)

        intent = IntentBuilder('GetFirstAppointmentIntent')\
            .one_of('AppointmentKeyword', 'ScheduleKeyword')\
            .require('FirstKeyword')\
            .build()
        self.register_intent(intent, self.get_first)

    def initialize(self):
        self.schedule_event(self.__calendar_connect, datetime.now(),
                            name='calendar_connect')
        self.schedule_repeating_event(self.check_reminders, datetime.now(),
                                     30, name='reminders')
    
    @intent_file_handler('SynchroniseCalendar.intent')
    def sync_event_reminders(self, msg=None):
        #Get first ten events
        LOG.info("Searching for reminders...")
        now = datetime.utcnow()
        now_iso = now.isoformat() + 'Z' 
        eventsResult = self.service.events().list(
            calendarId='primary', timeMin=now_iso, maxResults=10,
            singleEvents=True, orderBy='startTime').execute()
        events = eventsResult.get('items', [])

        for e in events:
            self.add_reminder(e)
        self.speak("Yay calendar synced! :D")
    
    def add_reminder(self, event):
        events.append(event)
        event_summary = event['summary']
        reminders = event['reminders']
        LOG.info("reminders from event: {}".format(reminders))

        reminder_list = []
        if 'useDefault' in reminders:
            reminder_default = reminders['useDefault']
            if reminder_default:
                reminder_list.append(10)
        
        #check for custom created reminders
        if 'overrides' in reminders:
            reminder_override = reminders['overrides']
            for rem in reminder_override:
                reminder_list.append(rem['minutes'])
        # r = {"reminders": reminder_list, "event": event}
        temp_dict = {event_summary:{"reminders": reminder_list, "event": event}}
        event_reminders.update(temp_dict)
        LOG.info("temp_dict: {}".format(temp_dict))
        LOG.info("Reminders: {}".format(event_reminders))

    def check_reminders(self):
        LOG.info("Checking reminders")
        e_reminder = event_reminders.copy()
        for event_summary in e_reminder:
            value = e_reminder[event_summary]
            reminder_list = value["reminders"]
            if reminder_list == []:
                LOG.info("no reminders for {}".format(event_summary))
                pass
            else:
                event = value["event"] 
                event_start = event['start'].get('dateTime')
                e_start = datetime.fromisoformat(event_start)
                for reminder in reminder_list:
                    #If the reminder is not handled perform some checks
                    remind_time = e_start - timedelta(minutes=reminder) # get the time when you want to remind the user
                    now = to_local_tz(datetime.utcnow()) #Get current local time
                    remaining_minutes = self.convert_to_minutes(remind_time, now) #calculate the remaining minutes
                    LOG.info("User will be reminded for {} in {} minutes".format(event_summary, remaining_minutes))
                    
                    if now > remind_time:
                        #Send the user a reminder                          
                        play_wav(REMINDER_PING)
                        # handled_reminders[event_summary][str(e_start)]['handled'].append(reminder)
                        data={'summary':event_summary,'time':reminder}
                        self.speak_dialog("Reminder",data)
                        reminder_list.remove(reminder)


    # def check_event_reminders(self, msg=None):
    #     LOG.info("Searching for reminders...")
    #     now = datetime.utcnow()
    #     now_iso = now.isoformat() + 'Z' 
    #     tomorrow = (now + timedelta(days=1)).replace(hour=0,minute=0,second=0).isoformat() + 'Z'  # 'Z' indicates UTC time
        
    #     #Get first ten events
    #     eventsResult = self.service.events().list(
    #         calendarId='primary', timeMin=now_iso, maxResults=10,
    #         singleEvents=True, orderBy='startTime').execute()
    #     events = eventsResult.get('items', [])
        
    #     if not events:
    #         LOG.info("[Event_reminders] - no events today...")
    #     else:
    #         for event in events:
    #             if not is_wholeday_event(event): #only check the non wholeday events
    #                 self.add_reminder(event)
        
    # def add_reminder(self, event):
    #     #get the start_time and convert from UTC ISO format to datetime format
    #     event_start = event['start'].get('dateTime')
    #     e_start = datetime.fromisoformat(event_start)
        
    #     #construct dict to keep track of already handled reminders
    #     event_summary = event['summary']
    #     if not event_summary in handled_reminders:
    #         handled_reminders[event_summary] = {}
    #     if not str(e_start) in handled_reminders[event_summary]:
    #         handled_reminders[event_summary][str(e_start)] = {}
    #     if not 'handled' in handled_reminders[event_summary][str(e_start)]:
    #         handled_reminders[event_summary][str(e_start)]['handled'] = []
                 
    #     #retrieve the list of reminders known by google calendar
    #     #check for default reminders
    #     event_reminders = event['reminders']
    #     reminder_list = []
    #     if 'useDefault' in event_reminders:
    #         reminder_default = event_reminders['useDefault']
    #         if reminder_default:
    #             reminder_list.append(10)
        
    #     #check for custom created reminders
    #     if 'overrides' in event_reminders:
    #         reminder_override = event_reminders['overrides']
    #         for rem in reminder_override:
    #             reminder_list.append(rem['minutes'])
        
    #     for reminder in reminder_list:
    #         #check if reminder is already handled
    #         if reminder in handled_reminders[event_summary][str(e_start)]['handled']:
    #             LOG.debug(f"reminder {reminder} for {event_summary} already handled!")
    #         else:
    #             #If the reminder is not handled perform some checks
    #             remind_time = e_start - timedelta(minutes=reminder) # get the time when you want to remind the user
    #             now = to_local_tz(datetime.utcnow()) #Get current local time
    #             remaining_minutes = self.convert_to_minutes(remind_time, now) #calculate the remaining minutes
                
    #             if now > remind_time:
    #                 #Send the user a reminder                          
    #                 play_wav(REMINDER_PING)
    #                 handled_reminders[event_summary][str(e_start)]['handled'].append(reminder)
    #                 data={'summary':event_summary,'time':reminder}
    #                 self.speak_dialog("Reminder",data)
            
    def convert_to_minutes(self, first_datetime, second_datetime):
        # Calculate the difference between two time variables in minutes  
        time_delta = (first_datetime - second_datetime)
        total_seconds = time_delta.total_seconds()
        minutes = total_seconds/60
        return minutes

    def get_event_today(self, msg=None):
        now = datetime.utcnow()
        now_iso = now.isoformat() + 'Z' 
        tomorrow = (now + timedelta(days=1)).replace(hour=0,minute=0,second=0).isoformat() + 'Z'  # 'Z' indicates UTC time
        
        eventsResult = self.service.events().list(
            calendarId='primary', timeMin=now_iso, timeMax=tomorrow, maxResults=10,
            singleEvents=True, orderBy='startTime').execute()
        events = eventsResult.get('items', [])

        if not events:
            self.speak_dialog('NoNextAppointments')
        else:
            #get first 5 events of today
            for event in events:
                LOG.debug(event)
                event_start = event['start'].get('dateTime')
                event_d = datetime.strptime(remove_tz(event_start), '%Y-%m-%dT%H:%M:%S')
                
                if not is_wholeday_event(event):
                    start = event['start'].get('dateTime')
                    d = datetime.strptime(remove_tz(start), '%Y-%m-%dT%H:%M:%S')
                    starttime = nice_time(d, self.lang, True, self.use_24hour,
                                        True)
                    startdate = d.strftime('%-d %B')
                else:
                    start = event['start']['date']
                    d = datetime.strptime(start, '%Y-%m-%d')
                    startdate = d.strftime('%-d %B')
                    starttime = None
                # Speak result
                if starttime is None:
                    if d.date() == datetime.today().date():
                        data = {'appointment': event['summary']}
                        self.speak_dialog('NextAppointmentWholeToday', data)
                    elif is_tomorrow(d):
                        data = {'appointment': event['summary']}
                        self.speak_dialog('NextAppointmentWholeTomorrow', data)
                    else:
                        data = {'appointment': event['summary'],
                                'date': startdate}
                        self.speak_dialog('NextAppointmentWholeDay', data)
                elif d.date() == datetime.today().date():
                    data = {'appointment': event['summary'],
                            'time': starttime}
                    self.speak_dialog('NextAppointment', data)
                elif is_tomorrow(d):
                    data = {'appointment': event['summary'],
                            'time': starttime}
                    self.speak_dialog('NextAppointmentTomorrow', data)
                else:
                    data = {'appointment': event['summary'],
                            'time': starttime,
                            'date': startdate}
                    self.speak_dialog('NextAppointmentDate', data)

    def get_next(self, msg=None):
        now = datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
        eventsResult = self.service.events().list(
            calendarId='primary', timeMin=now, maxResults=10,
            singleEvents=True, orderBy='startTime').execute()
        events = eventsResult.get('items', [])

        if not events:
            self.speak_dialog('NoNextAppointments')
        else:
            event = events[0]
            LOG.debug(event)
            if not is_wholeday_event(event):
                start = event['start'].get('dateTime')
                d = datetime.strptime(remove_tz(start), '%Y-%m-%dT%H:%M:%S')
                starttime = nice_time(d, self.lang, True, self.use_24hour,
                                      True)
                startdate = d.strftime('%-d %B')
            else:
                start = event['start']['date']
                d = datetime.strptime(start, '%Y-%m-%d')
                startdate = d.strftime('%-d %B')
                starttime = None
            # Speak result
            if starttime is None:
                if d.date() == datetime.today().date():
                    data = {'appointment': event['summary']}
                    self.speak_dialog('NextAppointmentWholeToday', data)
                elif is_tomorrow(d):
                    data = {'appointment': event['summary']}
                    self.speak_dialog('NextAppointmentWholeTomorrow', data)
                else:
                    data = {'appointment': event['summary'],
                            'date': startdate}
                    self.speak_dialog('NextAppointmentWholeDay', data)
            elif d.date() == datetime.today().date():
                data = {'appointment': event['summary'],
                        'time': starttime}
                self.speak_dialog('NextAppointment', data)
            elif is_tomorrow(d):
                data = {'appointment': event['summary'],
                        'time': starttime}
                self.speak_dialog('NextAppointmentTomorrow', data)
            else:
                data = {'appointment': event['summary'],
                        'time': starttime,
                        'date': startdate}
                self.speak_dialog('NextAppointmentDate', data)

    def speak_interval(self, start, stop, max_results=None):
        eventsResult = self.service.events().list(
            calendarId='primary', timeMin=start, timeMax=stop,
            singleEvents=True, orderBy='startTime',
            maxResults=max_results).execute()
        events = eventsResult.get('items', [])
        if not events:
            LOG.debug(start)
            d = datetime.strptime(start.split('.')[0], '%Y-%m-%dT%H:%M:%SZ')
            if is_today(d):
                self.speak_dialog('NoAppointmentsToday')
            elif is_tomorrow(d):
                self.speak_dialog('NoAppointmentsTomorrow')
            else:
                self.speak_dialog('NoAppointments')
        else:
            for e in events:
                if is_wholeday_event(e):
                    data = {'appointment': e['summary']}
                    self.speak_dialog('WholedayAppointment', data)
                else:
                    start = e['start'].get('dateTime', e['start'].get('date'))
                    d = datetime.strptime(remove_tz(start),
                                             '%Y-%m-%dT%H:%M:%S')
                    starttime = nice_time(d, self.lang, True, self.use_24hour,
                                          True)
                    if is_today(d) or is_tomorrow(d) or True:
                        data = {'appointment': e['summary'],
                                'time': starttime}
                        self.speak_dialog('NextAppointment', data)

    def get_day(self, msg=None):
        d = extract_datetime(msg.data['utterance'])[0]
        d = d.replace(hour=0, minute=0, second=1, tzinfo=None)
        d_end = d.replace(hour=23, minute=59, second=59, tzinfo=None)
        d = d.isoformat() + 'Z'
        d_end = d_end.isoformat() + 'Z'
        self.speak_interval(d, d_end)
        return

    def get_first(self, msg=None):
        d = extract_datetime(msg.data['utterance'])[0]
        d = d.replace(hour=0, minute=0, second=1, tzinfo=None)
        d_end = d.replace(hour=23, minute=59, second=59, tzinfo=None)
        d = d.isoformat() + 'Z'
        d_end = d_end.isoformat() + 'Z'
        self.speak_interval(d, d_end, max_results=1)

    @property
    def utc_offset(self):
        return timedelta(seconds=self.location['timezone']['offset'] / 1000)

    @intent_file_handler('Schedule.intent')
    def add_new(self, message=None):
        title = self.get_response('whatsTheNewEvent')
        start = self.get_response('whenDoesItStart')
        end = self.get_response('whenDoesItEnd')
        if title and start and end:
            st = extract_datetime(start)
            et = extract_datetime(end)
            if st and et:
                st = st[0] - self.utc_offset
                et = et[0] - self.utc_offset
                self.add_calendar_event(title, start_time=st, end_time=et)

    @intent_file_handler('ScheduleAt.intent')
    def add_new_quick(self, msg=None):
        title = msg.data.get('appointmenttitle', None)
        if title is None:
            self.log.debug("NO TITLE")
            return

        st = extract_datetime(msg.data['utterance'])[0] # start time
        # convert to UTC
        st -= timedelta(seconds=self.location['timezone']['offset'] / 1000)
        et = st + timedelta(hours=1)
        self.add_calendar_event(title, st, et)

    def add_calendar_event(self, title, start_time, end_time, summary=None):
        start_time = start_time.strftime('%Y-%m-%dT%H:%M:00')
        stop_time = end_time.strftime('%Y-%m-%dT%H:%M:00')
        stop_time += UTC_TZ
        event = {}
        event['summary'] = title
        event['start'] = {
            'dateTime': start_time,
            'timeZone': 'UTC'
        }
        event['end'] = {
            'dateTime': stop_time,
            'timeZone': 'UTC'
        }
        data = {'appointment': title}
        try:
            self.service.events()\
                .insert(calendarId='primary', body=event).execute()
            self.speak_dialog('AddSucceeded', data)
        except:
            self.speak_dialog('AddFailed', data)


def create_skill():
    return GoogleCalendarSkill()
