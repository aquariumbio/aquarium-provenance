import logging
from aquarium.provenance import (
    CollectionEntity,
    ItemEntity,
    OperationActivity,
    PartEntity,
    PlanTrace
)
from aquarium.trace.visitor import ProvenanceVisitor

"""
Functions to check whether provenance is well constructed.
"""


class CheckVisitor(ProvenanceVisitor):
    def __init__(self, *, trace: PlanTrace = None, stop_list):
        self.__no_error = True
        self.__stop_list = stop_list
        super().__init__(trace)

    def visit_operation(self, operation: OperationActivity):
        for arg in operation.get_inputs():
            if arg.is_item() and not self.trace.has_item(arg.item_id):
                msg = "argument %s of operation %s is not in the trace"
                logging.warning(msg, arg.item_id, operation.operation_id)
                self.__no_error = False

    def visit_collection(self, collection: CollectionEntity):
        if collection.item_id in self.__stop_list:
            return

        for part in collection.parts():
            if not self.trace.has_item(part.item_id):
                logging.warning("Part %s not in trace", part.item_id)
                self.__no_error = False

        if not collection.generator:
            logging.warning("%s %s has no generators",
                            collection.item_type, collection.item_id)
            self.__no_error = False
        else:
            self.__check_generator(collection)

    def visit_item(self, item: ItemEntity):
        if item.item_id in self.__stop_list:
            return

        if not item.sample:
            logging.warning("%s %s has no sample",
                            item.item_type, item.item_id)
            self.__no_error = False

        if not item.generator:
            logging.warning("%s %s has no generators",
                            item.item_type, item.item_id)
            self.__no_error = False
        else:
            self.__check_generator(item)

    def visit_part(self, part: PartEntity):
        if part.item_id in self.__stop_list:
            return

        if not part.sample:
            logging.warning("%s %s has no sample",
                            part.item_type, part.item_id)
            self.__no_error = False

        # part should have generator if collection does
        if not part.generator:
            if part.collection.generator:
                logging.warning("%s %s has no generators",
                                part.item_type, part.item_id)
                self.__no_error = False
        else:
            self.__check_generator(part)

        if not part.sources:
            if not self.trace.has_item(part.collection.item_id):
                logging.warning("%s %s has collection %s not in trace",
                                part.item_type,
                                part.item_id,
                                part.collection.item_id)
                self.__no_error = False
            if part.collection.sources:
                logging.warning("%s %s has no sources, but %s does",
                                part.item_type,
                                part.item_id,
                                part.collection.item_id)
                self.__no_error = False

    def __check_generator(self, entity):
        if entity.generator.is_job():
            if entity.generator.job_id not in self.trace.jobs:
                msg = "job %s is a generator for %s %s but is not in trace"
                logging.warning(msg,
                                entity.generator.job_id,
                                entity.item_type,
                                entity.item_id)
                self.__no_error = False
            for op in entity.generator.operations:
                if op.operation_id not in self.trace.operations:
                    msg = "op %s in job %s a generator for %s %s not in trace"
                    logging.warning(msg,
                                    op.operation_id,
                                    entity.generator.job_id,
                                    entity.item_type,
                                    entity.item_id)
                    self.__no_error = False
        elif entity.generator.operation_id not in self.trace.operations:
            msg = "operation %s is a generator for %s %s but is not in trace"
            logging.warning(msg,
                            entity.generator.operation_id,
                            entity.item_type,
                            entity.item_id)
            self.__no_error = False

    def __check_sources(self, entity):
        for source_id in entity.get_source_ids():
            if not self.trace.has_item(source_id):
                logging.warning("source %s for %s %s is not in trace",
                                source_id, entity.item_type, entity.item_id)
                self.__no_error = False

# TODO: replace these with visitors