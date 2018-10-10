import argparse
import logging
from csv import DictWriter
from pydent import AqSession
from aquarium.provenance import (CollectionEntity, PartEntity, PlanTrace)
from aquarium.trace_factory import TraceFactory
from aquarium.trace_patch import create_trace_fix_visitor
from aquarium.trace_visitor import ProvenanceVisitor

from resources import resources


class YeastGatesPlateVisitor(ProvenanceVisitor):

    def __init__(self, *, trace, file):
        self.trace = trace
        fieldnames = ['Refname', 'Well', 'OD 600.0:nanometer', 'Container ID',
                      'Aliquot ID', 'Aliquot Name', 'OD600-48h',
                      'pick_replicate', 'TargetOD', 'Sample_ID', 'Gate',
                      'control_replicate', 'RNA Container ID']
        self.writer = DictWriter(file, fieldnames)

    def visit_collection(self, collection: CollectionEntity):
        op = self.trace.get_operations(input=collection)

        self.writer.writeheader()
        for part in collection.parts:
            part.apply(self)

    def visit_part(self, part: PartEntity):
        part_dict = dict()
        part_dict['Refname'] = 'flow-plate'
        part_dict['Well'] = part.part_ref
        part_dict['Container ID'] = part.collection.item_id
        part_dict['Aliquot ID'] = part.item_id
        if part.sample:
            part_dict['Sample_ID'] = str(part.sample.id)
            part_dict['Aliquot Name'] = part.sample.name

        self.writer.writerow(part_dict)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--plan_id',
                        help='the ID of the plan', required=True)
    parser.add_argument('-c', '--collection_id',
                        help="the ID of the plate", required=True)
    args = parser.parse_args()

    session = AqSession(
        resources['aquarium']['login'],
        resources['aquarium']['password'],
        resources['aquarium']['aquarium_url']
    )

    logging_level = logging.INFO
    logging.basicConfig(filename='transfer.log',
                        filemode='w',
                        level=logging_level)

    plan = session.Plan.find(args.plan_id)
    if not plan:
        logging.error("plan %s not found", args.plan_id)
        exit(0)

    fix_visitor = create_trace_fix_visitor()
    trace: PlanTrace = TraceFactory.create_from(session=session,
                                                plan=plan,
                                                visitor=fix_visitor)

    plate = trace.get_item(args.collection_id)
    if not plate:
        logging.error("Plate %s not found in plan %s",
                      args.collection_id, args.plan_id)
        exit(0)

    filename = "./{}-{}.csv".format(args.plan_id, args.collection_id)
    with open(filename, 'w') as out_file:
        visitor = YeastGatesPlateVisitor(trace=trace, file=out_file)
        plate.apply(visitor)


if __name__ == "__main__":
    main()
