#!/usr/bin/env python

'''DBMaintenance: clean up old rows in the audit table.

This table grows very large otherwise.
'''


import datetime
import json
import logging
import os
import sys
import gzip
import shutil
import time

import sqlalchemy
import sqlalchemy.exc
import sqlalchemy.ext.declarative
import sqlalchemy.orm
from sqlalchemy.pool import StaticPool
from sqlalchemy.pool import NullPool

import tornado.options

import tinys3

Session = sqlalchemy.orm.sessionmaker()

_once = False

S3_ACCESS_KEY  = os.getenv('S3_ACCESS_KEY')
S3_SECRET_KEY  = os.getenv('S3_SECRET_KEY')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')


def connect_to_database(url):
    global _once
    assert not _once
    _once = True

    engine = sqlalchemy.create_engine(url, poolclass=sqlalchemy.pool.NullPool, echo=False)
    Session.configure(bind=engine)


def upload_to_s3(file_path):
    if (S3_ACCESS_KEY == None or S3_SECRET_KEY == None or S3_BUCKET_NAME == None):
        logging.warn('No S3 credentials specified, ignoring upload request')
        return

    conn = tinys3.Connection(S3_ACCESS_KEY,S3_SECRET_KEY,tls=True)

    file_name = os.path.basename(file_path)

    with open(file_path,'rb') as file_obj:
        conn.upload(file_name, file_obj, S3_BUCKET_NAME)


def compress_file(file_path):
    time_str = time.strftime("%Y%m%d-%H%M%S")
    file_path_out = '%s-%s.gz' % (file_path, time_str)

    with open(file_path, 'rb') as file_in, gzip.open(file_path_out, 'wb') as file_out:
        shutil.copyfileobj(file_in, file_out)
    logging.info('File compressed at %s', file_path_out)

    return file_path_out


def copy_audit_to_file(session, file_path):
    logging.info('Copying all records in audit table to file %s', file_path)

    copy_sql = "COPY audit TO '%s' WITH CSV HEADER; DELETE FROM audit;" % file_path
    connection = session.connection().connection
    cursor = connection.cursor()

    # with open(file_path, 'wb') as audit_file:
    #     cursor.copy_to(audit_file, 'audit')
    cursor.execute(copy_sql)
    
    connection.commit()

    cursor.close()
    connection.close()


def main():
    logging.root.setLevel(logging.INFO)

    database_url = os.getenv('DATABASE_URL', 'postgres:///mitro')
    connect_to_database(database_url)
    extra_args = tornado.options.parse_command_line()

    file_path = '/tmp/audit.csv'
    session = Session()
    copy_audit_to_file(session, file_path)

    gzip_file_path = compress_file(file_path)
    upload_to_s3(gzip_file_path)


if __name__ == '__main__':
    main()
