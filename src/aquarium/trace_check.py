import logging


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
    elif not entity.sample:
        logging.warning("%s %s has no sample",
                        entity.item_type, entity.item_id)
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
