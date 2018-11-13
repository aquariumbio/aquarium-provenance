import logging
import re

from aquarium.provenance import (
    CollectionEntity,
    FileEntity,
    PlanTrace
)
from aquarium.trace.visitor import ProvenanceVisitor, FactoryVisitor


class CollectionSourceInferenceVisitor(ProvenanceVisitor):
    """
    Applies heuristic to add sources to the collection based on the sources of
    the parts of the collection
    """

    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_collection(self,
                         collection_entity: CollectionEntity):

        if collection_entity.sources:
            return

        entity_id = collection_entity.item_id
        parts = [entity for _, entity in self.trace.items.items()
                 if entity.is_part()
                 and entity.collection.item_id == entity_id]

        sources = set()
        for part in parts:
            for source in part.sources:
                if source.is_part():
                    source = source.collection
                if source.item_id not in sources:
                    logging.info("using part routing to add source %s to %s",
                                 source.item_id, entity_id)
                    collection_entity.add_source(source)
                    sources.add(source.item_id)


class FileSourcePruningVisitor(ProvenanceVisitor):
    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_file(self, file: FileEntity):
        self.prune_file_sources(file)

    def prune_file_sources(self, file_entity: FileEntity):
        """
        Replaces the sources for a FileEntity with a single source.

        A file should only have one source, but depending on associations more
        than one source may be captured.
        This heuristic chooses a source item whose ID is in the name of the
        file.

        Specific case is plate_reader data in yeast gates
        """
        if not file_entity.sources:
            return

        match = re.search('item(_|)([0-9]+)_', file_entity.name)
        if not match:
            return

        file_item_id = match.group(2)
        id_list = [source.item_id for source in file_entity.sources]
        if file_item_id not in id_list:
            msg = "Item id %s from filename %s not in sources %s for file %s"
            logging.error(msg, file_item_id, file_entity.name,
                          str(id_list), file_entity.id)

        if not self.trace.has_item(file_item_id):
            logging.error("Item ID %s does not exist in trace", file_item_id)
            return

        source = self.trace.get_item(file_item_id)
        file_entity.sources = [source]


class FilePrefixVisitor(ProvenanceVisitor):
    """
    A FileVisitor that adds the file ID as a prefix to the file name.

    Used to avoid name conflicts in situations where files with the same name
    may be written to the same directory.
    An example of this situation is when calibration beads are measured using
    the flow cytometer and the generated file is named A01.fcs.
    Saving this file to the same directory as the cytometry readings for a well
    plate will result in a file name conflict with the first entry in the well.
    """

    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_file(self, file_entity: FileEntity):
        if file_entity.is_external():
            logging.debug("File %s %s is external, not changing name",
                          file_entity.id, file_entity.name)
            return

        logging.debug("Visiting file %s %s to add prefix",
                      file_entity.id, file_entity.name)

        prefix = file_entity.upload_id
        file_entity.name = "{}-{}".format(prefix, file_entity.name)
        logging.debug("changing name of %s to %s",
                      file_entity.id, file_entity.name)


def create_patch_visitor():
    visitor = FactoryVisitor()
    visitor.add_visitor(FixMessageVisitor())
    visitor.add_visitor(FileSourcePruningVisitor())
    visitor.add_visitor(CollectionSourceInferenceVisitor())
    visitor.add_visitor(FilePrefixVisitor())
    return visitor


class FixMessageVisitor(ProvenanceVisitor):
    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_plan(self, plan: PlanTrace):
        logging.info("Applying heuristic fixes to plan %s", plan.plan_id)
