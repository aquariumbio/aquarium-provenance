import json
import logging
import os


class UploadManager:

    def __init__(self, *, trace, basepath=None, bucket=None, s3=None):
        self.trace = trace
        self.basepath = None
        self.bucket = None
        self.s3 = None

    def configure(self, *, s3=None, bucket=None, basepath=None):
        if s3:
            self.s3 = s3
        if bucket:
            self.bucket = bucket
        if basepath:
            self.basepath = basepath

    def upload(self, *, activity, prov_only=False):
        self._put_provenance(path=self.basepath, trace=self.trace)
        if prov_only:
            return

        activity_id = activity.get_activity_id()
        file_list = self.trace.get_files(generator=activity)
        if not file_list:
            logging.error("No files for generator %s", activity_id)
            return

        dest_path = os.path.join(self.basepath, activity_id)
        self._upload_directory(path=dest_path, file_list=file_list)

    def _upload_directory(self, *, path, file_list):
        for file_entity in file_list:
            content_type = file_entity.upload.upload_content_type
            if file_entity.type == 'FCS':
                content_type = 'application/octet-stream'
            elif file_entity.type == 'CSV':
                content_type = 'text/csv'
            try:
                self._put_object(path=path,
                                 filename=file_entity.name,
                                 file_object=file_entity.upload.data,
                                 content_type=content_type)
            except ConnectionError:
                logging.error(
                    "Upload of file %s (%s) failed due to closed connection",
                    file_entity.file_id, file_entity.name
                )
                raise

    def _put_object(self, *, path, filename, file_object, content_type):
        key_path = os.path.join(path, filename)
        logging.info("upload %s to %s", key_path, self.bucket)
        self.s3.put_object(
            Body=file_object,
            Bucket=self.bucket,
            ContentType=content_type,
            Key=key_path
        )

    def _put_provenance(self, *, path, trace):
        self._put_object(
            path=path,
            filename='provenance_dump.json',
            file_object=str(json.dumps(trace.as_dict(), indent=2)),
            content_type='application/json'
        )


class S3DumpProxy:

    def __init__(self, root_dir):
        self.root_dir = root_dir

    def put_object(self, *, Body, Bucket, ContentType, Key):
        # make directory root_dir/Bucket/Key minus file name
        # write Body to
        path = os.path.join(*[self.root_dir, Bucket, Key])
        directory_path = os.path.join(*(str.split(path, os.sep)[:-1]))
        if ContentType == 'application/json':
            output = Body
        else:
            output = "would write file to {}".format(path)
        if not os.path.exists(directory_path):
            os.makedirs(directory_path)
        with open(path, 'w') as file:
            file.write(output)
