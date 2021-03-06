'''
Copyright 2017-present, Airbnb Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''

import base64
import json
import logging
import os
import re
import time

from stream_alert.handler import StreamAlert
# import all rules loaded from the main handler
import main

LOGGER_SA = logging.getLogger('StreamAlert')
LOGGER_CLI = logging.getLogger('StreamAlertCLI')
LOGGER_CLI.setLevel(logging.INFO)

DIR_RULES = 'test/integration/rules'
DIR_TEMPLATES = 'test/integration/templates'
COLOR_RED = '\033[0;31;1m'
COLOR_GREEN = '\033[0;32;1m'
COLOR_RESET = '\033[0m'

def report_output(cols, force_exit):
    """Helper function to pretty print columns
    Args:
        cols: A list of columns to print (test description, pass|fail)
        force_exit: Boolean to break exectuion of integration testing
    """
    print '\ttest: {: <40} {: >25}'.format(*cols)
    if force_exit:
        os._exit(1)

def test_rule(rule_name, test_file_contents):
    """Feed formatted records into StreamAlert and check for alerts
    Args:
        rule_name: The rule name being tested
        test_file_contents: The dictionary of the loaded test fixture file
    """
    # rule name header
    print '\n{}'.format(rule_name)

    for record in test_file_contents['records']:
        context = None
        event = {'Records': []}
        event['Records'].append(record['kinesis_data'])
        if record['trigger']:
            expected_alerts = 1
        else:
            expected_alerts = 0

        alerts = StreamAlert(return_alerts=True).run(event, context)
        # we only want alerts for the specific rule passed in
        matched_alerts = [x for x in alerts if x['rule_name'] == rule_name]

        if len(matched_alerts) == expected_alerts:
            result = '{}[Pass]{}'.format(COLOR_GREEN, COLOR_RESET)
            force_exit = False
        else:
            result = '{}[Fail]{}'.format(COLOR_RED, COLOR_RESET)
            force_exit = True

        report_output([record['description'], result], force_exit)

def format_record(test_record):
    """Create a properly formatted Kinesis, S3, or SNS record.

    Supports a dictionary or string based data record.  Reads in
    event templates from the test/integration/templates folder.

    Args:
        test_record: Test record metadata dict with the following structure:
            data - string or dict of the raw data
            trigger - bool of if the record should produce an alert
            source - which stream/s3 bucket originated the data
            service - which aws service originated the data

    Returns:
        populated dict in the format of the specific service.
    """
    service = test_record['service']
    source = test_record['source']

    data_type = type(test_record['data'])
    if data_type == dict:
        data = json.dumps(test_record['data'])
    elif data_type in (unicode, str):
        data = test_record['data']
    else:
        LOGGER_CLI.info('Invalid data type: %s', type(test_record['data']))
        return

    if service == 's3':
        pass

    elif service == 'kinesis':
        kinesis_path = os.path.join(DIR_TEMPLATES, 'kinesis.json')
        with open(kinesis_path, 'r') as kinesis_template:
            try:
                template = json.load(kinesis_template)
            except ValueError as err:
                LOGGER_CLI.error('Error loading kinesis.json: %s', err)
                return

        template['kinesis']['data'] = base64.b64encode(data)
        template['eventSourceARN'] = 'arn:aws:kinesis:us-east-1:111222333:stream/{}'.format(source)
        return template

    elif service == 'sns':
        pass

    else:
        LOGGER_CLI.info('Invalid service %s', service)

def check_keys(test_record):
    """Check the test_record contains the required keys

    Args:
        test_record: Test record metadata dict

    Returns:
        Boolean result of key set comparison
    """
    req_keys = {
        'data',
        'description',
        'service',
        'source',
        'trigger'
    }
    record_keys = set(test_record.keys())
    return req_keys == record_keys

def apply_helpers(test_record):
    """Detect and apply helper functions to test fixtures
    Helpers are declared in test fixtures via the following keyword:
    "<helpers:helper_name>"

    Supported helper functions:
        last_hour: return the current epoch time minus 60 seconds to pass the
                   last_hour rule helper.
    Args:
        test_record: loaded fixture file JSON as a dict.
    """
    # declare all helper functions here, they should always return a string
    helpers = {
        'last_hour': lambda: str(int(time.time()) - 60)
    }
    helper_regex = re.compile(r'\<helper:(?P<helper>\w+)\>')

    def find_and_apply_helpers(test_record):
        for key, value in test_record.iteritems():
            if isinstance(value, str) or isinstance(value, unicode):
                test_record[key] = re.sub(
                    helper_regex,
                    lambda match: helpers[match.group('helper')](),
                    test_record[key]
                )
            elif isinstance(value, dict):
                find_and_apply_helpers(test_record[key])

    find_and_apply_helpers(test_record)

def test_kinesis_alert_rules():
    """Integration test the 'Alert' Lambda function with Kinesis records"""
    for root, _, rule_files in os.walk(DIR_RULES):
        for rule_file in rule_files:
            rule_name = rule_file.split('.')[0]
            rule_file_path = os.path.join(root, rule_file)

            with open(rule_file_path, 'r') as rule_file_handle:
                try:
                    contents = json.load(rule_file_handle)
                except ValueError as err:
                    LOGGER_CLI.error('Error loading %s: %s', rule_file, err)
                    continue

            test_records = contents.get('records')
            if not test_records:
                LOGGER_CLI.error('Improperly formatted test file: %s', rule_file_path)
                continue

            if len(test_records) == 0:
                LOGGER_CLI.error('No records to test for %s', rule_name)
                continue

            for test_record in test_records:
                if not check_keys(test_record):
                    LOGGER_CLI.error('Improperly formatted test_record: %s',
                                    test_record)
                    continue
                apply_helpers(test_record)
                test_record['kinesis_data'] = format_record(test_record)

            test_rule(rule_name, contents)

def stream_alert_test(options):
    """Integration testing handler

    Args:
        options: dict of CLI options: (func, env, source)
    """
    if options.debug:
        LOGGER_SA.setLevel(logging.DEBUG)
    else:
        LOGGER_SA.setLevel(logging.INFO)

    if options.source == 'kinesis':
        if options.func == 'alert':
            test_kinesis_alert_rules()

        elif options.func == 'output':
            # TODO(jack) handle s3 event formatting
            pass

    elif options.source == 's3':
        if options.func == 'alert':
            # TODO(jack) handle s3 event formatting
            pass
