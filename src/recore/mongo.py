# -*- coding: utf-8 -*-
# Copyright © 2014 SEE AUTHORS FILE
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from pymongo import MongoClient
from bson.objectid import ObjectId
import urllib
import datetime

connection = None
database = None

def connect(host, port, user, password, db):
    # First, escape the parameters
    (n, p) = escape_credentials(user, password)
    connect_string = "mongodb://%s:%s@%s:%s/%s" % \
                     (n, p, host, port, db)
    connection = MongoClient(connect_string)
    db = connection[db]
    return (connection, db)

def lookup_project(d, project):
    """Given a mongodb database, `d`, search the 'projects' collection for
any documents which match the `project` key provided. `search_result`
is either a hash or `None` if no matches were found.
    """
    projects = d['projects']
    search_result = projects.find_one({'project': project})
    return search_result

def lookup_state(d, c_id):
    """`d` is a mongodb database and `c_id` is a correlation ID
corresponding to the ObjectID value in MongoDB."""
    pass

def initialize_state(d, project):
    """Initialize the state of a given project release"""
    # Just record the name now and insert an empty array to record the
    # result of steps. Oh, and when it started. Maybe we'll even add
    # who started it later!
    #
    # We expect this to return an ObjectID object. We don't care so
    # much about what we're `insert`ing into the state collection. `d`
    # would be a Mongo database object, but we can't unittest with a
    # real one, so we need mock one up, give a string for the project
    # name, and make sure the insert method returns a mocked ObjectID
    # which when `str`'d returns a reasonable value.
    state0 = {
        'project': project,
        'step_log': [],
        'created': datetime.datetime.utcnow()
    }
    id = d['state'].insert(state0)
    return id

def update_state(d, c_id, state):
    """`d` is a mongodb database, `c_id` is the ObjectID, and state is a
hash we will push onto the `step_log`."""
    _id = { '_id': ObjectID(str(c_id)) }
    _update = {
        '$push': {
            'step_log': state
            }
    }
    d['state'].update(
        _id,
        _update)



def escape_credentials(n, p):
    """Return the RFC 2396 escaped version of name `n` and password `p` in
a 2-tuple"""
    return (urllib.quote(n), urllib.quote(p))
