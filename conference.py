#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime
import time

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import TypeOfSession

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "CONF_FEAT_SPEAK"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

SESSION_DEFAULTS = {
    "name": "change me please!",
    "description": "you forgot to enter a description!",
    "highlights": ["default"],
    "startTime": "12:00",
    "sessionDate": "2016-01-01",
    "typeOfSession": "NOT_SPECIFIED",
    "speaker": "John Doe",
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

CONF_SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

CONF_SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
)

SESS_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SESS_DELETE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -
    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )


# - - - Sessions - - - - - - - - - - - - - - - - - - - - - -
    
    def _copySessionToForm(self, session):
        """Copy fields from Session object to SessionForm."""
        sf = SessionForm()
        wssk = session.key.urlsafe()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # format date/time fields to string for form.
                if field.name.endswith('Date') or field.name.endswith('Time'):
                    setattr(sf, field.name, str(getattr(session, field.name)))
                elif field.name == 'typeOfSession':
                    # lookup value of enum type
                    setattr(sf, field.name, getattr(TypeOfSession, getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, wssk)
        sf.check_initialized()
        return sf
        
    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='querySessions', http_method='GET', name='querySessions')
    def querySessions(self, request):
        """Get all Sessions."""
        q = Session.query()
        return SessionForms(
            items=[self._copySessionToForm(session) for session in q])
        
    
    @endpoints.method(CONF_GET_REQUEST, SessionForms,
        path='conference/{websafeConferenceKey}/session',
        http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Get sessions for the Conference."""
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found for key: %s' % wsck)
        sessions = Session.query(ancestor=conf.key)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])
        
    
    @endpoints.method(CONF_SESS_GET_REQUEST, SessionForms,
        path='conference/{websafeConferenceKey}/session/type/{typeOfSession}',
        http_method='POST', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """For a Conference, return all Sessions that match the given type."""
        wsck = request.websafeConferenceKey
        sessType = request.typeOfSession
        conf = ndb.Key(urlsafe=wsck).get()
        # check the provided key exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found for key: %s' % wsck)
        # find all sessions belonging to the conference key
        sessions = Session.query(ancestor=conf.key)
        sessions = sessions.filter(Session.typeOfSession == sessType)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])


    @endpoints.method(SESS_GET_REQUEST, SessionForms,
        path='session/speaker/{speaker}',
        http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return SessionForms of sessions given by speaker"""
        q = Session.query()
        q = q.filter(Session.speaker == request.speaker)
        return SessionForms(
            items=[self._copySessionToForm(session) for session in q])
    
    def _createSessionObject(self, request):
        """Create or Session object, returning SessionForm/request."""
        wsck = request.websafeConferenceKey
        
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # validate the required fields are entered.
        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")
        if not request.speaker:
            raise endpoints.BadRequestException("Session 'speaker' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        
        # default data in for fields with no values provided
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]
        
        if data['typeOfSession']:
            data['typeOfSession'] = str(data['typeOfSession'])

        # convert dates from strings to Date objects; set month based on start_date
        if data['sessionDate']:
            data['sessionDate'] = datetime.strptime(data['sessionDate'][:10], "%Y-%m-%d").date()
           
        # convert the string time to a time value
        data['startTime'] = datetime.strptime(data['startTime'][:5], "%H:%M").time()
        
        # remove the values we dont need anymore from the collection
        del data['websafeKey']
        del data['websafeConferenceKey']

        # load the conference the session is to be created for.
        conf = ndb.Key(urlsafe=wsck).get()

        # confirm the conference is valid.
        if not conf:
            raise endpoints.BadRequestException(
                "Conference for key %s does not exist" % wsck)

        # confirm the current user is the organiser of the conference.
        if conf.organizerUserId != user_id:
            raise endpoints.UnauthorizedException(
                'User is not organiser of conference')
                
        # generate keys for the session, linking back to parent 
        # conference
        c_key = ndb.Key(urlsafe=wsck)
        s_id = Session.allocate_ids(size=1, parent=c_key)[0]
        s_key = ndb.Key(Session, s_id, parent=c_key)
        data['key'] = s_key
        
        wssk = Session(**data).put().urlsafe()
        sess = ndb.Key(urlsafe=wssk).get()
        
        taskqueue.add(params={'websafeConferenceKey': wsck,
                              'websafeSessionKey': wssk},
                              url='/tasks/set_featured_speaker')
        
        return self._copySessionToForm(sess)
    
    
    @endpoints.method(CONF_SESS_POST_REQUEST, SessionForm,
        path='conference/{websafeConferenceKey}/session',
        http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new Session for a Conference"""
        return self._createSessionObject(request)
        
    
    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='queryNonWorkshopSessionsBefore7pm',
        http_method='GET', name='queryNonWorkshopSessionsBefore7pm')
    def queryNonWorkshopSessionsBefore7pm(self, request):
        """Get all non workshop sessions, with start time prior to 19:00"""
        latestStart = datetime.strptime('19:00', "%H:%M").time()
        sessions = Session.query(Session.startTime < latestStart)
        nonWorkshopSessions = [sess for sess in sessions
                               if sess.typeOfSession != 'WORKSHOP']
        return SessionForms(
            items = [self._copySessionToForm(sess) for sess in nonWorkshopSessions]
        )
        
        
# - - - - - - - Wishlist - - - - - - - -
    @endpoints.method(SESS_POST_REQUEST, ProfileForm,
        path='session/{websafeSessionKey}',
        http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add a Session to the users wishlist."""
        prof = self._getProfileFromUser()
        wssk = request.websafeSessionKey
        session = ndb.Key(urlsafe=wssk)
        if session.kind() != 'Session':
            raise endpoints.BadRequestException(
                'Not a valid Session key: %s' % wssk)
        else:
            session.get()
            
        if not session:
            raise endpoints.NotFoundException(
                'No Session found for key: %s' % wssk)

        if wssk not in prof.sessionKeysWishlist:
            prof.sessionKeysWishlist.append(wssk)
            prof.put()
            
        return self._copyProfileToForm(prof)

        
    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='getSessionsInWishlist',
        http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Get sessions in wishlist."""
        prof = self._getProfileFromUser()
        sessions = [ndb.Key(urlsafe=wssk).get() for wssk in prof.sessionKeysWishlist]
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions])
            
    
    @endpoints.method(SESS_DELETE_REQUEST, BooleanMessage,
        path='session/{websafeSessionKey}',
        http_method='DELETE', name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        """Delete the session from the user's wishlist."""
        prof = self._getProfileFromUser()
        wssk = request.websafeSessionKey
        # check if session exists in user's wishlist
        if wssk in prof.sessionKeysWishlist:
            # remove the session key from the wishlist
            prof.sessionKeysWishlist.remove(wssk)
            retval = True
            prof.put()
        else:
            # session key not in users wishlist, return value is false.
            retval = False
        # write back the changes to the datastore

        return BooleanMessage(data=retval)

# - - - - - Speaker - - - - - - 
    @staticmethod
    def _calculateFeaturedSpeaker(wsck, wssk):
        """Upon adding a new session, check if the new speaker should be the keynote."""
        # load the conference and session objects from websafe keys.
        conf = ndb.Key(urlsafe=wsck).get()
        sess = ndb.Key(urlsafe=wssk).get()
        
        # retrieve the current keynote speaker from memcache
        # key is CONF_FEAT_SPEAK_{wsck}
        memSpeakerAnn = memcache.get('_'.join((MEMCACHE_FEATURED_SPEAKER_KEY, wsck)))
        
        # if there is no memcache entry add it for this conference.
        if memSpeakerAnn is None:
            # this is the initial session of the conference.
            # pull out all of the sessions this speaker is giving
            speakerSessions = Session.query(ancestor=conf.key)
            speakerSessions.filter(Session.speaker == sess.speaker)
            
            # initialise the empty string
            sessionsString = ''
            
            # iterate over all of the speakers sessions
            for ss in speakerSessions:
                # add the session name to the string
                sessionsString += "%s," % (ss.name)
            
            sessionsString = sessionsString[:-1]
            speakerAnnString = '|'.join((sess.speaker, sessionsString))
            memcache.set('_'.join((MEMCACHE_FEATURED_SPEAKER_KEY, wsck)), speakerAnnString)
        else:       
            # retrieved string is {speakerName}|[{session.name},]
            memSpeakerSplit = memSpeakerAnn.split('|')
            
            # load the split values from array into more readable variables
            speakerFromMem = memSpeakerSplit[0]
        
            # speaker being added is different to the current keynote speaker,
            # see who has the higher session count and set memcache appropriately.
            if speakerFromMem != sess.speaker:
                # if the speaker being added is different to the current keynote,
                # check the counts of both
            
                # get the count of the newly added speaker
                q1 = Session.query(ancestor=conf.key)
                q1 = q1.filter(Session.speaker == sess.speaker)
            
                # get the count of the current keynote speaker
                q2 = Session.query(ancestor=conf.key)
                q2 = q2.filter(Session.speaker == speakerFromMem)
            
                # if the newly added sessions speaker has more sessions, set to be the keynote.
                if q1.count > q2.count:
                    # initialise an empty string to append each session to.
                    sessionsString = ''
            
                    # iterate over all of the speakers sessions
                    for ss in q1:
                        # add the session name to the string
                        sessionsString += "%s," % ss.name
                
                    # trim the trailing comma
                    sessionsString = sessionsString[:-1]
                    # join the speaker name and sessions
                    speakerAnnString = '|'.join((sess.speaker, sessionsString))
                    # set the memcache entry to the final announcement string for the speaker.
                    memcache.set('_'.join((MEMCACHE_FEATURED_SPEAKER_KEY, wsck)), speakerAnnString)
            else: 
                # current speaker is already set to be featured. retreive the memcache string
                # and append the new session name to the string with a leading comma.
                memSpeakerAnn = memcache.get('_'.join((MEMCACHE_FEATURED_SPEAKER_KEY, wsck)))
                memcache.set('_'.join((MEMCACHE_FEATURED_SPEAKER_KEY, wsck)), ','.join((memSpeakerAnn, sess.name)))
                
          
    @endpoints.method(CONF_GET_REQUEST, StringMessage,
        path='conference/{websafeConferenceKey}/featuredspeaker',
        http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return the featured (keynote) speaker for the conference."""
        wsck = request.websafeConferenceKey
        speaker = memcache.get('_'.join((MEMCACHE_FEATURED_SPEAKER_KEY, wsck)))
        # this check helps incase this endpoint is hit before a session is added.
        if speaker is None:
            speaker = 'No keynote speaker.'
        return StringMessage(data=speaker)
        

# - - - - - Additional Queries - - - - - -
    @endpoints.method(SESS_TYPE_GET_REQUEST, SessionForms,
        path='getAllSessionsByType',
        http_method='GET', name='getAllSessionsByType')
    def getAllSessionsByType(self, request):
        """Return all Sessions, regardless of Conference, optional filter by type and speaker"""
        
        # open new query on Session
        q = Session.query()
        # apply speaker filter if provided
        if request.speaker is not None:
            q = q.filter(Session.speaker == request.speaker)
        # apply session type filter if provided
        if request.typeOfSession is not None:
            q = q.filter(Session.typeOfSession == request.typeOfSession)
        # fetch the results
        q = q.fetch()
        # return the results as session forms objects to display.
        return SessionForms(
            items=[self._copySessionToForm(sess)
                   for sess in q])


    @endpoints.method(CONF_SESS_GET_REQUEST, SessionForms,
        path='/conference/{websafeConferenceKey}/top',
        http_method='GET', name='getTopSessionsForConference')
    def getWorkshopsStartingSoonForConference(self, request):
        """Return three sessions starting soon for a Conference"""
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        
        # check the provided conference exists.
        if not conf:
            raise endpoints.NotFoundException(
                'Invalid Conference ID: %s' % wsck)

        # Get current time.
        currentTime = datetime.now().time()
        # Get todays date in YYYY-MM-DD format
        currentDate = datetime.now().date()
        # set the times to zeroes in a string
        tmpDateString = currentDate.strftime('%Y-%m-%d 00:00:00')
        # convert back to a datetime value to compare against ndb dateproperty (time zeroed out)
        currentDate = datetime.strptime(tmpDateString, '%Y-%m-%d %H:%M:%S')
        
        # find all WORKSHOPS for the conference starting today that haven't started yet.
        q = Session.query(ancestor=conf.key)
        q = q.filter(Session.sessionDate == currentDate)
        # only filter on type if the parameter is entered.
        if request.typeOfSession is not None:
            q = q.filter(Session.typeOfSession == request.typeOfSession)
        q = q.filter(Session.startTime > currentTime)
        # order the results so the ones starting soon appear first
        q = q.order(Session.startTime)
        # only fetch the top results results.
        q = q.fetch(3)

        # provide the results back to the forms.
        return SessionForms(items=[self._copySessionToForm(sess) for sess in q])
        
        
api = endpoints.api_server([ConferenceApi]) # register API