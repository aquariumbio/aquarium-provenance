import datetime
import json
import os
from collections import defaultdict


class UploadManager:

    def __init__(self, *, trace, directory_map):
        self.trace = trace
        self.directory_map = directory_map
        self.basepath = None
        self.bucket = None
        self.s3 = None

    # TODO: move this to PlanTrace
    @staticmethod
    def create_from(*, trace):
        dir_map = defaultdict(list)
        for _, file_entity in trace.files.items():
            if not file_entity.generator:
                continue

            if file_entity.generator.is_job():
                generator_id = file_entity.generator.job_id
                gen_dir_name = "job_{}".format(generator_id)
            else:
                generator_id = file_entity.generator.operation_id
                gen_dir_name = "op_{}".format(generator_id)
            dir_map[gen_dir_name].append(file_entity)

        return UploadManager(trace=trace, directory_map=dir_map)

    def configure(self, *, s3, bucket, basepath):
        self.s3 = s3
        self.bucket = bucket
        date_str = datetime.date.today().strftime('%Y%m')
        self.basepath = os.path.join(*[basepath, date_str, self.trace.plan_id])

    def upload(self, *, prov_only=False):
        provenance_dump = self.trace.as_dict()
        for dir_name, entity_list in self.directory_map.items():
            dest_path = os.path.join(self.basepath, dir_name)
            if not prov_only:
                self._upload_directory(path=dest_path, entity_list=entity_list)
            key_path = os.path.join(dest_path, 'provenance_dump.json')
            print("upload {} to {}".format(key_path, self.bucket))
            self.s3.put_object(
                Body=str(json.dumps(provenance_dump, indent=2)),
                Bucket=self.bucket,
                ContentType='application/json',
                Key=key_path
            )

    def _upload_directory(self, *, path, entity_list):
        for file_entity in entity_list:
            if file_entity.type == 'FCS':
                content_type = 'application/octet-stream'
            elif file_entity.type == 'CSV':
                content_type = 'text/csv'
            key_path = os.path.join(path, file_entity.name)
            file_object = file_entity.upload.data
            print("upload {} to {}".format(key_path, self.bucket))
            self.s3.put_object(
                Body=file_object,
                Bucket=self.bucket,
                ContentType=content_type,
                Key=key_path
            )
