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

import logging
import recore.utils
import recore.job.create
import recore.job.step
import recore.job.status

def receive(ch, method, properties, body):
    # print "devops Topic: %s\nMessage: %s\n" % (method.routing_key, body,)
    #ch.basic_ack(properties.delivery_tag)
    # print "acked it"
    out = logging.getLogger('recore')
    notify = logging.getLogger('recore.stdout')
    msg = recore.utils.load_json_str(body)
    # print "message plz: %s" % msg
    topic = method.routing_key
    out.info('Received a new message via routing key %s' % topic)
    # print "routed: %s" % str(topic)
    out.debug("Message: %s" % msg)
    ##################################################################
    # NEW JOB
    #
    # FSM will get {"project": "$NAME"} with the topic of job.create
    # and a reply_to set
    #
    # FSM sends a msg with {"id": MongoObjectID} back to the
    # reply_to. This ObjectID is the same as the value of the _id
    # property of the new document created in the 'state' collection.
    ##################################################################
    if topic == 'job.create':
        # We need to get the name of the temporary queue to respond back on.
        notify.info("new job create")
        reply_to = properties.reply_to
        # print "reply to happened? %s" % reply_to
        out.info("New job requested, starting release process for %s ..." % (
            msg["project"]))
        id = recore.job.create.release(ch, msg['project'], reply_to)
        # print "got that id"
        recore.job.step.run(ch, msg['project'], id)
    elif topic == 'release.step':
        # Handles updates from the workers running jobs
        notify.info("Got a releaes step update")
        try:
            out.info("Updating release status...")
            recore.job.status.update(ch, method, properties, msg)
            app_id = properties.correlation_id
            correlation_id = properties.correlation_id
            # If the response was fail we need to halt the release
            if msg['status'] == 'failed':
                out.error("Job %s for release %s failed. Aborting release." % (
                    app_id, correlation_id))
                notify.info("Job %s for release %s failed. Aborting release." % (
                    app_id, correlation_id))
                # The release is no longer running
                recore.job.status.running(properties, False)
            if msg['status'] == 'completed':
                out.error("Job JOB_NAME_HERE for release %s failed. Aborting release." % (
                    app_id, correlation_id))
                notify.info("Job %s for release %s failed. Aborting release." % (
                    app_id, correlation_id))
                # if there are no more steps mark running as false
                #recore.job.status.running(properties, False)
                # TODO: execute the next step
                pass
        except Exception, e:
            notify.info(str(e))
            out.error("Error occured: %s" % e)
        out.info("Release step finished")
        notify.info("We finished processing the update")
    else:
        out.warn("Unknown routing key %s. Doing nothing ...")
        notify.info("IDK what this is: %s" % topic)
        # TODO: This is a glorified case/switch statement. There needs
        # to be a better way to map topics to functions. Consider
        # refactoring (later, lol) to a Python module-tree that
        # mimicks the topic hierarchy. Then just pass some
        # intelligently named function everything we know?
    notify.info("end receive() routine")
    out.debug("end receive() routine")
