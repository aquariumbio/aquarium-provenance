import argparse
import boto3
import json
import logging
import sys
from botocore.client import Config
from pydent import AqSession
from aquarium.provenance import check_trace
from aquarium.trace_factory import (TraceFactory)
from resources import resources
from aquarium.trace_upload import UploadManager, S3DumpProxy
from aquarium.trace_patch import fix_trace


def find_igem_plate(trace):
    generator_id = None
    for op_id, op_activity in trace.operations.items():
        if op_activity.operation_type.name == '2. Resuspension and Outgrowth':
            generator_id = op_id
            break

    if not generator_id:
        return None

    for _, entity in trace.items.items():
        if entity.generator and not entity.generator.is_job():
            if entity.generator.operation_id == generator_id:
                standard = entity.get_attribute('standard')
                if standard and standard == 'IGEM_protocol':
                    return entity


def main():
    desc = "Generate provenance and/or transfer files to TACC"
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument("-p", "--plan_id",
                        help="the ID of the plan",
                        required=True)
    parser.add_argument("-c", "--challenge_problem",
                        choices=['yg', 'nc', 'ps'],
                        help="the challenge problem",
                        required=True)
    parser.add_argument("-o", "--output",
                        action="store_true",
                        help="write provenance to file")
    parser.add_argument("-v", "--validate",
                        action="store_true",
                        help="check that provenance features are complete")
    parser.add_argument("-u", "--upload",
                        action="store_true",
                        help="upload files to TACC S3")
    parser.add_argument("--prov_only",
                        action="store_true",
                        help="upload only provenance files")
    parser.add_argument("--debug",
                        action="store_true",
                        help="set log level to debug instead of info")
    parser.add_argument("--dump",
                        help="directory to dump files instead of uploading")
    parser.add_argument("--no_fix",
                        action="store_true",
                        help="do not apply heuristic fixes to provenance")
    args = parser.parse_args()

    session = AqSession(
        resources['aquarium']['login'],
        resources['aquarium']['password'],
        resources['aquarium']['aquarium_url']
    )

    logging_level = logging.INFO
    if args.debug:
        logging_level = logging.DEBUG
    log_filename = str(args.plan_id) + '-provenance.log'
    logging.basicConfig(filename=log_filename,
                        filemode='w',
                        level=logging_level)

    plan = session.Plan.find(args.plan_id)

    logging.info("Creating provenance")
    trace = TraceFactory.create_from(session=session, plan=plan)
    trace.add_attribute({'lab': 'UW_BIOFAB'})

    if args.challenge_problem == 'yg':
        trace.add_attribute({'challenge_problem': 'YEAST_GATES'})
    elif args.challenge_problem == 'nc':
        trace.add_attribute({'challenge_problem': 'NOVEL_CHASSIS'})
    elif args.challenge_problem == 'ps':
        trace.add_attribute({'challenge_problem': 'PROTEIN_DESIGN'})

    stop_list = [item.item_id for item in trace.get_inputs()]

    if not args.no_fix:
        logging.info("Applying heuristic fixes to provenance")
        # TODO: figure out why have to exclude IGEM plate from fix stop list
        fix_trace(trace, stop_list)

    if args.validate:
        logging.info("Checking provenance")
        # IGEM plate is special case because produced but inputs not captured
        igem_plate = find_igem_plate(trace)
        if igem_plate:
            stop_list.append(igem_plate.item_id)
        if not check_trace(trace=trace, stop_list=stop_list):
            print("Errors in provenance, check log for detail",
                  file=sys.stderr)

    if args.output:
        filename = "{}-{}-provenance.json".format(
            args.challenge_problem, args.plan_id)
        with open(filename, 'w') as file:
            file.write(json.dumps(trace.as_dict(), indent=2))

    if args.upload or args.dump:
        manager = UploadManager.create_from(trace=trace)
        if args.dump:
            print("Creating local dump")
            s3_client = S3DumpProxy(args.dump)
        else:
            print("Uploading to TACC")
            s3_client = boto3.client(
                's3',
                endpoint_url="{}://{}".format(resources['s3']['S3_PROTO'],
                                              resources['s3']['S3_URI']),
                aws_access_key_id=resources['s3']['S3_KEY'],
                aws_secret_access_key=resources['s3']['S3_SECRET'],
                config=Config(signature_version=resources['s3']['S3_SIG']),
                region_name=resources['s3']['S3_REGION'],
            )
        manager.configure(
            s3=s3_client,
            bucket='uploads',
            basepath='biofab'
        )
        manager.upload(prov_only=args.prov_only)


if __name__ == "__main__":
    main()
