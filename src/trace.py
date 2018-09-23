import argparse
import boto3
import json
import logging
import sys
from botocore.client import Config
from pydent import AqSession
from aquarium.trace_check import check_trace
from aquarium.trace_factory import (TraceFactory)
from resources import resources
from aquarium.trace_upload import UploadManager, S3DumpProxy
from aquarium.trace_patch import (
    create_trace_fix_visitor,
    ChallengeProblemTraceVisitor
)


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
    # TODO: https://stackoverflow.com/a/41881271/3593355
    parser.add_argument("--date",
                        help="the date to use for the key, e.g., 201808")
    args = parser.parse_args()

    print("plan {}".format(args.plan_id))

    session = AqSession(
        resources['aquarium']['login'],
        resources['aquarium']['password'],
        resources['aquarium']['aquarium_url']
    )

    base_filename = "{}-{}-provenance".format(
        args.challenge_problem, args.plan_id)

    logging_level = logging.INFO
    if args.debug:
        logging_level = logging.DEBUG
    log_filename = "{}.log".format(base_filename)
    logging.basicConfig(filename=log_filename,
                        filemode='w',
                        level=logging_level)

    plan = session.Plan.find(args.plan_id)

    logging.info("Creating provenance")
    if args.no_fix:
        fix_visitor = None
    else:
        fix_visitor = create_trace_fix_visitor()
        fix_visitor.add_visitor(ChallengeProblemTraceVisitor(
            labname='UW_BIOFAB',
            challenge_problem=args.challenge_problem))
    trace = TraceFactory.create_from(session=session,
                                     plan=plan,
                                     visitor=fix_visitor)

    if args.validate:
        logging.info("Checking provenance")
        stop_list = [item.item_id for item in trace.get_inputs()]
        # IGEM plate is special case because produced but inputs not captured
        igem_plate = find_igem_plate(trace)
        if igem_plate:
            stop_list.append(igem_plate.item_id)
        # TODO: make sure that cp and experiment ref attributes are set
        if not check_trace(trace=trace, stop_list=stop_list):
            msg = "Errors in provenance for plan {}, check log for detail"
            print(msg.format(args.plan_id), file=sys.stderr)

    if args.output:
        filename = "{}.json".format(base_filename)
        with open(filename, 'w') as file:
            file.write(json.dumps(trace.as_dict(), indent=2))

    if args.upload or args.dump:
        manager = UploadManager.create_from(trace=trace)
        if args.dump:
            print("Creating local dump of {}".format(args.plan_id))
            s3_client = S3DumpProxy(args.dump)
        else:
            print("Uploading plan {} to TACC".format(args.plan_id))
            s3_client = boto3.client(
                's3',
                endpoint_url="{}://{}".format(resources['s3']['S3_PROTO'],
                                              resources['s3']['S3_URI']),
                aws_access_key_id=resources['s3']['S3_KEY'],
                aws_secret_access_key=resources['s3']['S3_SECRET'],
                config=Config(signature_version=resources['s3']['S3_SIG']),
                region_name=resources['s3']['S3_REGION'],
            )
        if args.date:
            manager.configure(
                s3=s3_client,
                bucket='uploads',
                basepath='biofab',
                date_str=args.date
            )
        else:
            manager.configure(
                s3=s3_client,
                bucket='uploads',
                basepath='biofab'
            )
        manager.upload(prov_only=args.prov_only)
        print("plan {} complete".format(args.plan_id))


if __name__ == "__main__":
    main()
