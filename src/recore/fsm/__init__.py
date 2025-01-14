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

from bson.objectid import ObjectId
import json
from datetime import datetime as dt
import recore.mongo
import recore.amqp
import logging
import threading
import pika.spec
import pika.exceptions
import pymongo.errors


class FSM(threading.Thread):
    """The re-core Finite State Machine to oversee the execution of
a project's release steps."""

    def __init__(self, state_id, *args, **kwargs):
        """Not really overriding the threading init method. Just describing
        the parameters we expect to receive when initialized and
        setting up logging.

        `state_id` - MongoDB ObjectID of the document holding release steps
        """
        super(FSM, self).__init__(*args, **kwargs)
        self.app_logger = logging.getLogger('FSM-%s' % state_id)
        self.app_logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s:%(funcName)s:%(lineno)d - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        self.app_logger.addHandler(handler)

        # properties for later when we run() like the wind
        self.ch = None
        self.conn = None
        self.state_id = state_id
        self._id = {'_id': ObjectId(self.state_id)}
        self.state = {}
        self.dynamic = {}
        self.reply_queue = None

    def run(self):  # pragma: no cover
        try:
            self._run()
        except pika.exceptions.ConnectionClosed:
            # Don't know why, but pika likes to raise this exception
            # when we intentionally close a connection...
            self.app_logger.debug("Closed AMQP connection")
        self.app_logger.info("Terminating")
        return True

    def _run(self):
        self._setup()
        try:
            # Pop a step off the remaining steps queue
            # - Reflect in MongoDB
            self.dequeue_next_active_step()
            self.app_logger.debug("Dequeued next active step. Updated currently active step.")
        except IndexError:
            # The previous step was the last step
            self.app_logger.debug("Processed all remaining steps for job with id: %s" % self.state_id)
            self.app_logger.debug("Cleaning up after release")
            # Now that we're done, clean up that queue and record end time
            self._cleanup()
            return True

        # Parse the step into a message for the worker queue
        props = pika.spec.BasicProperties()
        props.correlation_id = self.state_id
        props.reply_to = self.reply_queue

        params = self.active['parameters']
        msg = {
            'project': self.project,
            'parameters': params,
            'dynamic': self.dynamic
        }
        plugin_queue = "worker.%s" % self.active['plugin']

        # Send message to the worker with instructions and dynamic data
        self.ch.basic_publish(exchange='',
                              routing_key=plugin_queue,
                              body=json.dumps(msg),
                              properties=props)

        self.app_logger.info("Sent plugin new job details")

        # Begin consuming from reply_queue
        self.app_logger.debug("Waiting for plugin to update us")

        for method, properties, body in self.ch.consume(self.reply_queue):
            self.ch.basic_ack(method.delivery_tag)
            self.ch.cancel()
            self.on_started(self.ch, method, properties, body)

    def on_started(self, channel, method_frame, header_frame, body):
        self.app_logger.info("Plugin 'started' update received. "
                             "Waiting for next state update")
        self.app_logger.debug("Waiting for completed/errored message")

        # Consume from reply_queue, wait for completed/errored message
        for method, properties, body in self.ch.consume(self.reply_queue):
            self.ch.basic_ack(method.delivery_tag)
            self.ch.cancel()
            self.on_ended(self.ch, method, properties, body)

    def on_ended(self, channel, method_frame, header_frame, body):
        self.app_logger.debug("Got completed/errored message back from the worker")

        msg = json.loads(body)
        self.app_logger.debug(json.dumps(msg))

        # Remove from active step, push onto completed steps
        # - Reflect in MongoDB
        if msg['status'] == 'completed':
            self.app_logger.info("State update received: Job finished without error")
            self.move_active_to_completed()
            self._run()
        else:
            self.app_logger.error("State update received: Job finished with error(s)")
            return False

    def move_active_to_completed(self):
        finished_step = self.active
        self.completed.append(finished_step)
        self.active = None

        _update_state = {
            '$set': {
                'active_step': self.active,
                'completed_steps': self.completed
            }
        }
        self.update_state(_update_state)

    def dequeue_next_active_step(self):
        """Take the next remaining step off the queue and move it into active
        steps.
        """
        self.active = self.remaining.pop(0)
        _update_state = {
            '$set': {
                'active_step': self.active,
                'remaining_steps': self.remaining
            }
        }
        self.update_state(_update_state)

    def update_state(self, new_state):
        """
        Update the state document in Mongo for this release
        """
        try:
            _id_update_state = self.state_coll.update(self._id,
                                                      new_state)

            if _id_update_state:
                self.app_logger.debug("Updated 'currently running' task")
            else:
                self.app_logger.error("Failed to update 'currently running' task")
                raise Exception("Failed to update 'currently running' task")
        except pymongo.errors.PyMongoError, pmex:
            self.app_logger.error(
                "Unable to update state with %s. "
                "Propagating PyMongo error: %s" % (new_state, pmex))
            raise pmex

    def _cleanup(self):
        self.ch.queue_delete(queue=self.reply_queue)
        self.app_logger.debug("Deleted AMQP queue: %s" % self.reply_queue)
        self.conn.close()
        self.app_logger.debug("Closed AMQP connection")

        _update_state = {
            '$set': {
                'ended': dt.now()
            }
        }

        try:
            self.update_state(_update_state)
            self.app_logger.debug("Recorded release end time: %s" %
                                  _update_state['$set']['ended'])
        except Exception, e:
            self.app_logger.error("Could not set 'ended' item in state document")
            raise e
        else:
            self.app_logger.debug("Cleaned up all leftovers. We should terminate next")

    def _connect_mq(self):
        mq = recore.amqp.MQ_CONF
        creds = pika.credentials.PlainCredentials(mq['NAME'], mq['PASSWORD'])
        connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=str(mq['SERVER']),
            credentials=creds))
        self.app_logger.debug("Connection to MQ opened.")
        channel = connection.channel()
        self.app_logger.debug("MQ channel opened. Declaring exchange ...")
        channel.exchange_declare(exchange=mq['EXCHANGE'],
                                 durable=True,
                                 exchange_type='topic')
        self.app_logger.debug("Exchange declared.")
        result = channel.queue_declare(queue='',
                                       exclusive=True,
                                       durable=False)
        self.reply_queue = result.method.queue
        return (channel, connection)

    def _setup(self):
        try:
            self.state.update(recore.mongo.lookup_state(self.state_id))
        except TypeError:
            self.app_logger.error("The given state document could not be located: %s" % self.state_id)
            raise LookupError("The given state document could not be located: %s" % self.state_id)

        try:
            if not self.ch and not self.conn:
                self.app_logger.debug("Opening AMQP connection and channel for the first time")
                (self.ch, self.conn) = self._connect_mq()
        except Exception, e:
            self.app_logger.error("Couldn't connect to AMQP")
            raise e

        self.project = self.state['project']
        self.dynamic.update(self.state['dynamic'])
        self.completed = self.state['completed_steps']
        self.active = self.state['active_step']
        self.remaining = self.state['remaining_steps']
        self.db = recore.mongo.database
        self.state_coll = self.db['state']
