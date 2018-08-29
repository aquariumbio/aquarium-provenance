"""
Aquarium provenance classes derived using pydent models.
See TraceFactory.create_from to load.

Loosely based on provenance ontology, which includes activities, agents,
and entities.

Note that I punted on properly modeling which kinds of entities in Aquarium.
An Item has a sample and object_type;
a collection has no sample but has an object_type; and
a part of a collection has a sample but no object_type.
"""
import abc
import logging
import os
from collections import defaultdict, deque
from copy import copy


class AttributesMixin(abc.ABC):
    """
    Defines an abstract class to serve as a mixin for classes with objects that
    carry attributes.

    In Aquarium, only a Plan, Item and Operation may carry data associations
    from which these are populated, so only apply these to the corresponding
    classes.
    """

    @abc.abstractmethod
    def __init__(self):
        self.attributes = dict()
        super().__init__()

    def add_attribute(self, attribute):
        for key, value in attribute.items():
            if value:
                self.attributes[key] = value

    def get_attribute(self, key):
        if key in self.attributes:
            return self.attributes[key]

    def as_dict(self):
        attr_dict = dict()
        if self.attributes:
            attr_dict['attributes'] = self.attributes
        return attr_dict


class AbstractEntity(abc.ABC):
    """
    Defines an abstract class with the properties of an entity from the
    perspective of provenance.
    Specifically, has the generating operations, and a list of source entities.
    """

    @abc.abstractmethod
    def __init__(self):
        self.generator = None
        self.sources = set()
        super().__init__()

    def add_generator(self, activity):
        self.generator = activity

    def add_source(self, entity):
        self.sources.add(entity)

    def get_source_ids(self):
        return [item_entity.item_id for item_entity in self.sources]

    def as_dict(self):
        entity_dict = dict()
        if self.generator:
            generator_dict = dict()
            if self.generator.is_job():
                generator_dict = self.generator.as_dict()
            else:  # is operation
                generator_dict['operation_id'] = self.generator.operation_id
            entity_dict['generated_by'] = generator_dict
        source_ids = self.get_source_ids()
        if source_ids:
            entity_dict['sources'] = source_ids
        return entity_dict


class AbstractItemEntity(AbstractEntity, AttributesMixin):
    """
    Defines an abstract entity representing an item.
    """

    @abc.abstractmethod
    def __init__(self, *, item_id, item_type):
        self.item_id = str(item_id)
        self.item_type = item_type
        super().__init__()

    def as_dict(self):
        item_dict = dict()
        item_dict['item_id'] = self.item_id
        item_dict['type'] = self.item_type
        entity_dict = AbstractEntity.as_dict(self)
        attr_dict = AttributesMixin.as_dict(self)
        return {**item_dict, **{**entity_dict, **attr_dict}}

    def is_collection(self):
        return False

    def is_item(self):
        return False

    def is_part(self):
        return False


class ItemEntity(AbstractItemEntity):
    """
    Defines an entity class for an Aquarium Item object.
    """

    def __init__(self, *, item):
        self.item = item
        self.sample = item.sample
        self.object_type = item.object_type
        super().__init__(item_id=item.id, item_type='item')

    def apply(self, visitor):
        visitor.visit_item(self)

    def as_dict(self):
        item_dict = super().as_dict()
        sample_dict = dict()
        sample_dict['sample_id'] = str(self.sample.id)
        sample_dict['sample_name'] = self.sample.name
        item_dict['sample'] = sample_dict
        type_dict = dict()
        type_dict['object_type_id'] = str(self.object_type.id)
        type_dict['object_type_name'] = self.object_type.name
        item_dict['object_type'] = type_dict
        return item_dict

    def get_sample(self):
        return self.sample

    def is_item(self):
        return True


class CollectionEntity(AbstractItemEntity):
    """
    Defines an entity class for an Aquarium Collection object.
    """

    def __init__(self, collection):
        self.object_type = collection.object_type
        self.collection = collection
        self.parts = list()
        super().__init__(item_id=collection.id, item_type='collection')

    def add_part(self, part):
        self.parts.append(part)

    def apply(self, visitor):
        visitor.visit_collection(self)

    def as_dict(self):
        item_dict = super().as_dict()
        type_dict = dict()
        type_dict['object_type_id'] = str(self.object_type.id)
        type_dict['object_type_name'] = self.object_type.name
        item_dict['object_type'] = type_dict
        return item_dict

    def is_collection(self):
        return True


class PartEntity(AbstractItemEntity):

    def __init__(self, *, part_id: str, sample, collection: CollectionEntity):
        self.sample = sample
        self.collection = collection
        self.collection.add_part(self)
        super().__init__(item_id=part_id, item_type='part')

    @property
    def part_ref(self):
        return self.item_id.split('/')[1]

    def get_sample(self):
        return self.sample

    def apply(self, visitor):
        visitor.visit_part(self)

    def as_dict(self):
        item_dict = super().as_dict()
        item_dict['part_of'] = self.collection.item_id
        sample_dict = dict()
        sample_dict['sample_id'] = str(self.sample.id)
        sample_dict['sample_name'] = self.sample.name
        item_dict['sample'] = sample_dict
        return item_dict

    def is_part(self):
        return True


class FileEntity(AbstractEntity):
    """
    Defines an entity class for a file
    (corresponds to an Aquarium Upload object).

    Note that a file should only have one source.
    """

    def __init__(self, *, upload):
        self.file_id = str(upload.id)
        self.name = upload.name
        self.size = upload.size
        self.upload = upload
        super().__init__()

    def add_source(self, source):
        # Add source ID as prefix to avoid name conflicts
        # should only be one source for a file
        if source in self.sources:
            return

        prefix = source.item_id
        if source.is_part():
            prefix = source.collection.item_id
        self.name = "{}-{}".format(prefix, self.name)

        super().add_source(source)

    def apply(self, visitor):
        visitor.visit_file(self)

    def file_type(self):
        _, extension = os.path.splitext(self.name)
        if extension == '.fcs':
            return 'FCS'
        if extension == '.csv':
            return 'CSV'
    type = property(file_type)

    def as_dict(self, *, path=None):
        file_dict = super().as_dict()
        file_dict['file_id'] = self.file_id
        # TODO: figure out how to prefix name by generator based path when needed
        file_dict['filename'] = self.get_path(directory=path)
        file_dict['size'] = self.size
        if self.type:
            file_dict['type'] = self.type
        return file_dict

    def get_path(self, *, directory=None):
        name = self.name
        if directory:
            name = os.path.join(directory, name)
        return name


class OperationArgument(abc.ABC):
    """
    Models an argument to an operation, which can be either a
    (though use it to capture output during trace conversion)
    """

    @abc.abstractmethod
    def __init__(self, *, name: str, field_value_id: str):
        self.name = name
        self.field_value_id = str(field_value_id)

    def is_item(self):
        """
        Return true if this argument is an input item or collection, and
        false, otherwise.
        """
        return False

    def as_dict(self):
        arg_dict = dict()
        arg_dict['name'] = self.name
        arg_dict['field_value_id'] = self.field_value_id
        return arg_dict


class OperationParameter(OperationArgument):

    def __init__(self, *, name: str, field_value_id: str, value):
        self.value = value
        super().__init__(name=name, field_value_id=field_value_id)

    def as_dict(self):
        arg_dict = super().as_dict()
        arg_dict['value'] = self.value
        return arg_dict


class OperationInput(OperationArgument):

    def __init__(self, *, name, field_value_id, item, routing_id=None):
        self.item_id = item.item_id
        self.item = item
        self.routing_id = routing_id
        super().__init__(name=name, field_value_id=field_value_id)

    def is_item(self):
        return True

    def as_dict(self):
        arg_dict = super().as_dict()
        arg_dict['item_id'] = self.item_id
        if self.routing_id:
            arg_dict['routing_id'] = self.routing_id
        return arg_dict


class JobActivity:
    def __init__(self, *, job_id, operations):
        self.job_id = job_id
        self.operations = operations

    def is_job(self):
        return True

    def as_dict(self):
        job_dict = dict()
        job_dict['job_id'] = self.job_id
        job_dict['operations'] = [op.operation_id for op in self.operations]
        return job_dict


class OperationActivity(AttributesMixin):

    def __init__(self, operation):
        self.type = 'operation'
        self.operation_id = str(operation.id)
        self.operation_type = operation.operation_type
        self.operation = operation
        self.inputs = list()
        super().__init__()

    def apply(self, visitor):
        visitor.visit_operation(self)

    def add_input(self, input: OperationArgument):
        self.inputs.append(input)

    def has_input(self, item_entity: ItemEntity):
        for arg in self.inputs:
            if arg.is_item() and arg.item_id == item_entity.item_id:
                return True
        return False

    def as_dict(self):
        op_dict = dict()
        op_dict['operation_id'] = self.operation_id
        op_type = dict()
        op_type['operation_type_id'] = str(self.operation_type.id)
        op_type['category'] = self.operation_type.category
        op_type['name'] = self.operation_type.name
        op_dict['operation_type'] = op_type
        op_dict['inputs'] = [input.as_dict() for input in self.inputs]
        attr_dict = AttributesMixin.as_dict(self)
        return {**op_dict, **attr_dict}

    def is_measurement(self):
        if self.attributes and 'measurement_operation' in self.attributes:
            return self.attributes['measurement_operation']
        return False

    def is_job(self):
        return False


class PlanTrace(AttributesMixin):

    def __init__(self, *, plan_id: str, name: str):
        self.plan_id = str(plan_id)
        self.plan_name = name
        self.operations = dict()
        self.jobs = dict()
        self.items = dict()
        self.files = dict()
        self.input_list = defaultdict(list)  # inverted list: item->op
        super().__init__()

    def add_file(self, file_entity):
        logging.debug("Adding file %s to trace", file_entity.file_id)
        self.files[file_entity.file_id] = file_entity

    def add_item(self, item_entity):
        logging.debug("Adding %s %s to trace",
                      item_entity.item_type, item_entity.item_id)
        self.items[item_entity.item_id] = item_entity

    def add_input(self, item_id, op_activity):
        self.input_list[item_id].append(op_activity)

    def add_operation(self, operation):
        logging.debug("Adding operation %s to trace", operation.operation_id)
        self.operations[operation.operation_id] = operation

    def add_job(self, job):
        logging.debug("Adding job %s to trace", job.job_id)
        self.jobs[job.job_id] = job

    def has_item(self, item_id):
        return bool(item_id) and str(item_id) in self.items

    def has_file(self, file_id):
        return bool(file_id) and str(file_id) in self.files

    def has_operation(self, operation_id):
        return bool(operation_id) and str(operation_id) in self.operations

    def get_item(self, item_id):
        return self.items[str(item_id)]

    def get_operation(self, operation_id):
        return self.operations[operation_id]

    def get_operations(self, item_id):
        """
        Get operations that have the item as an input
        """
        return self.input_list[item_id]

    def get_file(self, file_id):
        return self.files[str(file_id)]

    def get_inputs(self):
        return [item for _, item in self.items.items()
                if not item.sources and not item.generator]

    def apply(self, visitor):
        visitor.visit_trace(self)
        for _, operation in self.operations.items():
            operation.apply(visitor)
        for _, item in self.items.items():
            item.apply(visitor)
        for _, file in self.files.items():
            file.apply(visitor)

    def as_dict(self):
        trace_dict = dict()
        trace_dict['plan_id'] = self.plan_id
        trace_dict['plan_name'] = self.plan_name
        trace_dict['plan_inputs'] = [
            item.item_id for item in self.get_inputs()]
        trace_dict['operations'] = [op.as_dict()
                                    for _, op in self.operations.items()]
        trace_dict['jobs'] = [job.as_dict() for _, job in self.jobs.items()]
        trace_dict['items'] = [item.as_dict()
                               for _, item in self.items.items()]
        trace_dict['files'] = [file.as_dict()
                               for _, file in self.files.items()]
        super_dict = super().as_dict()
        return {**trace_dict, **super_dict}

    def project_from(self, activity):
        trace = PlanTrace(plan_id=self.plan_id, name=self.plan_name)
        trace.attributes = copy(self.attributes)
        operation_queue = deque()
        item_queue = deque()
        if activity.is_job():
            trace.add_job(activity)
            for _, entity in self.files.items():
                if entity.generator.is_job():
                    if entity.generator.job_id == activity.job_id:
                        trace.add_file(entity)
                        item_queue.extend(entity.sources)
            operation_queue.extend(activity.operations)
        else:
            operation_queue.append(activity)
            for _, entity in self.files.items():
                if entity.generator and not entity.generator.is_job():
                    if entity.generator.operation_id == activity.operation_id:
                        trace.add_file(entity)
                        item_queue.extend(entity.sources)

        visited_operations = set()
        visited_items = set()
        while operation_queue or item_queue:
            while operation_queue:
                op = operation_queue.popleft()
                if op.operation_id in visited_operations:
                    continue
                trace.add_operation(op)
                inputs = [self.get_item(input.item_id)
                          for input in op.inputs if input.is_item()]
                item_queue.extend(inputs)
                visited_operations.add(op.operation_id)

            while item_queue:
                item = item_queue.popleft()
                if item.item_id in visited_items:
                    continue
                trace.add_item(item)
                if item.generator:
                    operation_queue.append(item.generator)
                if item.is_part():
                    item_queue.append(item.collection)
                elif item.is_collection():
                    item_queue.extend(item.parts)
                item_queue.extend(item.sources)
                visited_items.add(item.item_id)

        return trace


def check_operation(trace, operation):
    no_error = True
    for arg in operation.inputs:
        if arg.is_item() and not trace.has_item(arg.item_id):
            msg = "argument %s of operation %s is not in the trace"
            logging.warning(msg, arg.item_id, operation.operation_id)
            no_error = False
    return no_error


def check_item(trace, entity, stop_list):
    no_error = True
    if entity.item_id in stop_list:
        return no_error

    if entity.is_collection():
        for part in entity.parts:
            if not trace.has_item(part.item_id):
                logging.warning(
                    "Part %s not in trace", part.item_id)
                no_error = False

    if not entity.generator:
        logging.warning("%s %s has no generators",
                        entity.item_type, entity.item_id)
        no_error = False
    else:
        if entity.generator.is_job():
            if entity.generator.job_id not in trace.jobs:
                msg = "job %s is a generator for %s %s but is not in trace"
                logging.warning(msg,
                                entity.generator.job_id,
                                entity.item_type,
                                entity.item_id)
                no_error = False
            for op in entity.generator.operations:
                if op.operation_id not in trace.operations:
                    msg = "operation %s in job %s a generator for %s %s not in trace"
                    logging.warning(msg,
                                    op.operation_id,
                                    entity.generator.job_id,
                                    entity.item_type,
                                    entity.item_id)
                    no_error = False
        elif entity.generator.operation_id not in trace.operations:
            msg = "operation %s is a generator for %s %s but is not in trace"
            logging.warning(msg,
                            entity.generator.operation_id,
                            entity.item_type,
                            entity.item_id)
            no_error = False

    if not entity.sources:
        if entity.is_part():
            if not trace.has_item(entity.collection.item_id):
                logging.warning("%s %s has collection %s not in trace",
                                entity.item_type,
                                entity.item_id,
                                entity.collection.item_id)
                no_error = False
            if entity.collection.sources:
                logging.warning("%s %s has no sources, but %s does",
                                entity.item_type,
                                entity.item_id,
                                entity.collection.item_id)
                no_error = False
        else:
            logging.warning("%s %s has no sources",
                            entity.item_type, entity.item_id)
            no_error = False
    else:
        for source_id in entity.get_source_ids():
            if not trace.has_item(source_id):
                logging.warning("source %s for %s %s is not in trace",
                                source_id, entity.item_type, entity.item_id)
                no_error = False
    return no_error


def check_file(trace, entity):
    no_error = True
    if not entity.generator:
        logging.warning("%s %s has no generators",
                        entity.name, entity.file_id)
        no_error = False
    else:
        if entity.generator.is_job():
            if entity.generator.job_id not in trace.jobs:
                logging.warning("job %s is a generator for file %s but is not in trace",
                                entity.generator.job_id,
                                entity.file_id)
                no_error = False
            for op in entity.generator.operations:
                if op.operation_id not in trace.operations:
                    logging.warning("operation %s in job %s a generator for file %s not in trace",
                                    op.operation_id,
                                    entity.generator.job_id,
                                    entity.file_id)
                    no_error = False
        elif entity.generator.operation_id not in trace.operations:
            logging.warning("operation %s is a generator for file %s but is not in trace",
                            entity.generator.operation_id,
                            entity.file_id)
            no_error = False

    if not entity.sources:
        logging.warning("%s %s has no sources",
                        entity.name, entity.file_id)
        no_error = False
    elif len(entity.sources) > 1:
        logging.warning("%s %s has more than one source",
                        entity.name, entity.file_id)
    else:
        for source_id in entity.get_source_ids():
            if not trace.has_item(source_id):
                logging.warning("source %s for %s is not in trace",
                                source_id, entity.file_id)
                no_error = False
    return no_error


def check_trace(*, trace, stop_list=[]):
    logging.info("starting trace check")
    if not stop_list:
        input_items = trace.get_inputs()
        stop_list = [item.item_id for item in input_items]

    no_error = True
    for _, entity in trace.items.items():
        if not check_item(trace, entity, stop_list):
            no_error = False
    for _, entity in trace.files.items():
        if not check_file(trace, entity):
            no_error = False
    for _, activity in trace.operations.items():
        if not check_operation(trace, activity):
            no_error = False
    if no_error:
        logging.info("no trace errors found")
    else:
        logging.info("trace errors found")
    return no_error
