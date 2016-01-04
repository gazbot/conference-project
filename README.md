App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


## Project Tasks
### Task 1
`Session` and `SessionForm` have been implemented in `models.py`, `speaker` is 
defined as a String field. `Session`'s are linked to Conferences in multiple ways

1. Upon creation via parent/ancestor linking in NDB.
1. Field in the `Session` model for conferenceId saves the websafeConferenceKey.

### Task 2
A `Session` can be added/removed from a user's Wishlist, regardless of being registered
in the appropriate `Conference`.
The following functions have been implemented. `addSessionToWishlist(SessionKey)`, `getSessionsInWishlist()`, `deleteSessionInWishlist(SessionKey)`

### Task 3
`index.yaml` has been modified manually and the autogenerated option has been removed.

`Session` indices have been added to support the new endpoints (typeOfSession/speaker etc)

1. First query added is `getAllSessionsByType` to support displaying `Session` types, across all `Conference`'s. 
1. Second query added is `getWorkshopsStartingSoonForConference` to support a potential future feature, a portlet
   to display upcoming workshops starting soon for a conference, which could be a large TV display at the conference.

#### Query Problem
  The [Google Cloud Platform DataStore documentation][7] states: 'A query can have no more than one 
  not-equal filter, and a query that has one cannot have any other inequality filters.'. Because in-equality `!=` is treated
  similarly to `a > b OR a < b`, this limits how we filter on `sessionType != WORKSHOP AND sessionStart < 1900`. Depending on
  the quantity of data, My solution would be to query the datastore for on `sessionType != WORKSHOP` then iterate through
  the results and extract workshops with a `startTime < 1900`. When I say depending on the data, the second function should
  iterate over the least amount of rows as getting the datastore to perform the bigger query would be more efficient.
        
### Task 4
`/tasks/set_featured_speaker` has been implemented and executed via the Task Queue each time a new session is added.
`getFeaturedSpeaker` has been implemented.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
[7]: https://cloud.google.com/appengine/docs/python/datastore/queries