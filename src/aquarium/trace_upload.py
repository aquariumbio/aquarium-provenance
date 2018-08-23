import datetime
import json
import os


class UploadManager:

    def __init__(self, *, trace, directory_map):
        self.trace = trace
        self.directory_map = directory_map
        self.basepath = None
        self.bucket = None
        self.s3 = None

    @staticmethod
    def create_from(*, trace):
        dir_map = dict()
        for _, file_entity in trace.files.items():
            if not file_entity.generator:
                continue

            if file_entity.generator.is_job():
                generator_id = file_entity.generator.job_id
                gen_name = "job_{}".format(generator_id)
            else:
                generator_id = file_entity.generator.operation_id
                gen_name = "op_{}".format(generator_id)
            if gen_name not in dir_map:
                print("adding {}".format(gen_name))
                dir_map[gen_name] = trace.project_from(
                    file_entity.generator)

        return UploadManager(trace=trace, directory_map=dir_map)

    def configure(self, *, s3, bucket, basepath):
        self.s3 = s3
        self.bucket = bucket
        date_str = datetime.date.today().strftime('%Y%m')
        self.basepath = os.path.join(*[basepath, date_str, self.trace.plan_id])

    def upload(self, *, prov_only=False):
        self._put_provenance(path=self.basepath, trace=self.trace)
        for dir_name, trace in self.directory_map.items():
            dest_path = os.path.join(self.basepath, dir_name)
            if not prov_only:
                self._upload_directory(path=dest_path, entity_map=trace.files)
            self._put_provenance(path=dest_path, trace=trace)

    def _upload_directory(self, *, path, entity_map):
        for _, file_entity in entity_map.items():
            if file_entity.type == 'FCS':
                content_type = 'application/octet-stream'
            elif file_entity.type == 'CSV':
                content_type = 'text/csv'
            self._put_object(path=path,
                             filename=file_entity.name,
                             file_object=file_entity.upload.data,
                             content_type=content_type)

    def _put_object(self, *, path, filename, file_object, content_type):
        key_path = os.path.join(path, filename)
        print("upload {} to {}".format(key_path, self.bucket))
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
