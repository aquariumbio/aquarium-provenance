import json
import logging
import re
from aquarium.provenance import (CollectionEntity, PartEntity)
from aquarium.trace.visitor import ProvenanceVisitor
from util.plate import well_coordinates, coordinates_for
from collections.abc import Mapping


class AddPartsVisitor(ProvenanceVisitor):
    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_collection(self, collection: CollectionEntity):
        """
        Adds the parts for a collection.
        """
        logging.debug("Adding parts for collection %s", collection.item_id)
        upload_matrix = AddPartsVisitor.get_upload_matrix(collection)
        routing_matrix = AddPartsVisitor.get_routing_matrix(collection)
        self._create_parts(collection, upload_matrix, routing_matrix)

    def visit_part(self, part_entity: PartEntity):
        if part_entity.sources:
            return

        source_attribute = part_entity.get_attribute('source')
        if not source_attribute:
            logging.debug("Part %s has no source attribute")
            return
        logging.debug("Adding sources for part %s", part_entity.item_id)

        if isinstance(source_attribute, list):
            logging.debug("Source for part %s is a list", part_entity.item_id)
            self._get_sources_from_list(part_entity=part_entity,
                                        source_list=source_attribute)
        elif isinstance(source_attribute, str):
            logging.debug("Source for part %s is string %s",
                          part_entity.item_id,
                          source_attribute)
            self._get_sources_from_string(part_entity=part_entity,
                                          source_str=source_attribute)
        else:
            logging.error("Bad source type %s", type(source_attribute))

    def _get_sources_from_list(self, *, part_entity, source_list):
        for src_obj in source_list:
            source_entity = None
            source_id = str(src_obj['id'])
            source_entity = self.factory.get_item(item_id=source_id)

            if 'row' in src_obj:  # is a part
                source_entity = self.factory.get_part(
                    collection=source_entity,
                    row=src_obj['row'],
                    column=src_obj['column'])

            if not source_entity:
                logging.debug("Source %s for part %s not found",
                              json.dumps(src_obj), part_entity.item_id)
                return

            if AddPartsVisitor.samples_match(source=source_entity,
                                             target=part_entity):
                logging.debug("Sample mismatch for source %s of part %s",
                              source_entity.item_id, part_entity.item_id)
                part_entity.add_source(source_entity)

    def _get_sources_from_string(self, *, part_entity, source_str):
        logging.debug("Source is %s", source_str)
        source_entity = self._get_source(source_str)
        if not source_entity:
            logging.error("No source item found for %s", source_str)
            return

        if AddPartsVisitor.samples_match(source=source_entity,
                                         target=part_entity):
            logging.debug("Sample mismatch for source %s of part %s",
                          source_entity.item_id, part_entity.item_id)
            part_entity.add_source(source_entity)

    @staticmethod
    def samples_match(*, source, target):
        if not source.sample:
            logging.debug("Source %s for %s has no sample",
                          source.item_id, target.item_id)
            return False

        if target.sample and source.sample.id != target.sample.id:
            msg = "Source %s sample %s does not match " \
                "part %s sample %s"
            logging.error(msg, source.item_id,
                          source.sample.id,
                          target.item_id,
                          target.sample.id)
            return False

        logging.debug("Adding sample %s to part %s",
                      source.sample.id, target.item_id)
        target.sample = source.sample
        return True

    def _create_parts(self, collection, upload_matrix, routing_matrix):
        self._create_parts_from_samples(collection)
        if routing_matrix:
            self._create_parts_from_routing(collection, routing_matrix)
        if upload_matrix:
            self._create_parts_from_uploads(collection, upload_matrix)

    def _create_parts_from_samples(self, coll_entity):
        collection = self.factory.item_map[coll_entity.item_id]
        generator = coll_entity.generator
        for i in range(len(collection.matrix)):
            row = collection.matrix[i]
            for j in range(len(row)):
                sample = self.factory.get_sample(row[j])
                if not sample:
                    continue

                part_entity = self.factory.get_part(
                    collection=coll_entity,
                    row=i,
                    column=j,
                    sample=sample)
                if not part_entity:
                    logging.debug("No part for reference %s/%s",
                                  coll_entity.item_id, well_coordinates(i, j))
                    continue

                if generator and not part_entity.generator:
                    part_entity.add_generator(generator)

    def _create_parts_from_routing(self, entity, routing_matrix):
        for i in range(len(routing_matrix)):
            row = routing_matrix[i]
            for j in range(len(row)):
                routing_entry = row[j]
                if not routing_entry or not isinstance(routing_entry, Mapping):
                    continue

                part_entity = self.factory.get_part(collection=entity,
                                                    row=i,
                                                    column=j)
                if not part_entity:
                    logging.debug("No part for reference %s/%s",
                                  entity.item_id, well_coordinates(i, j))
                    continue

                source_id = AddPartsVisitor._get_source_id(routing_entry)
                if not source_id:
                    logging.debug("No source information for %s from %s",
                                  entity.item_id,
                                  part_entity.ref)
                    continue

                if entity.generator and not part_entity.generator:
                    part_entity.add_generator(entity.generator)

                # assumes this is from the part_data attribute
                # and visit_part can deal with the entries
                if isinstance(routing_entry, Mapping):
                    logging.debug("Adding %s to %s",
                                  json.dumps(routing_entry),
                                  part_entity.item_id)
                    part_entity.add_attribute(routing_entry)
                    continue

                # other cases
                source_entity = self._get_source(source_id)
                if source_entity:
                    if not AddPartsVisitor.samples_match(
                            source=source_entity, target=part_entity):
                        continue

                    part_entity.add_source(source_entity)
                    msg = "Adding %s %s as source for %s %s for ref %s"
                    logging.debug(msg, source_entity.item_type,
                                  source_entity.item_id,
                                  part_entity.item_type,
                                  part_entity.item_id,
                                  source_id)
                    if source_entity.is_item():
                        logging.debug(
                            "source %s %s is an item",
                            source_entity.item_type, source_entity.item_id)
                        part_entity.add_attribute(
                            {'source_reference': source_id})

                attributes = AddPartsVisitor._get_attributes(routing_entry)
                part_entity.add_attribute(attributes)

    def _create_parts_from_uploads(self, entity, upload_matrix):
        for i in range(len(upload_matrix)):
            row = upload_matrix[i]
            for j in range(len(row)):
                upload_id = row[j]

                if not upload_id or upload_id <= 0:
                    continue

                part_entity = self.factory.get_part(
                    collection=entity, row=i, column=j)

                if not part_entity:
                    logging.debug("No part for reference %s/%s",
                                  entity.item_id, well_coordinates(i, j))
                    continue

                if entity.generator and not part_entity.generator:
                    part_entity.add_generator(entity.generator)

                msg = "Part %s has upload %s"
                logging.debug(msg, part_entity.item_id, upload_id)
                file_entity = self.factory.get_file(upload_id=upload_id)
                if file_entity:
                    file_entity.add_source(part_entity)

    def _get_source(self, source_id):
        """
        Returns an entity for the ID, creating the entity if it does not
        already exist.

        Source IDs come from data associations of collections and indicate the
        source for a part.

        May have one of the forms
        - item_id
        - item_id/well
        - object_type_name/item_id/sample_id/well
        The latter form is used in cases where the item is not a collection,
        but consists of subparts that are not explicitly modeled.
        An example is a yeast plate with colonies.
        In this case, return the item.

        Some plans have a well of the form [[i,j]] that needs to be
        converted to alphanumeric form.

        This should not be necessary once part are first order in aquarium.
        """
        logging.debug("Getting entity for source_id %s", source_id)
        if self.trace.has_item(source_id):
            return self.trace.get_item(source_id)

        source_components = source_id.split('/')
        if len(source_components) not in [2, 4]:
            logging.error(
                "Expecting source with either 2 or 4 components, got %s",
                source_id)
            return None

        if not re.match("[0-9]+", source_components[0]):
            logging.error(
                "Expecting source beginning with item ID, got %s",
                source_components[0])
            return None

        if len(source_components) == 2:
            source_item_id = source_components[0]
            well = source_components[1]

            pattern = r"\[\[([0-9]+),[ \t]*([0-9]+)\]\]"
            match = re.match(pattern, source_components[1])
            if match:
                well = well_coordinates(int(match[1]), int(match[2]))

        elif len(source_components) == 4:
            source_item_id = source_components[1]
            well = source_components[3]

        source_item_entity = self.factory.get_item(item_id=source_item_id)

        if not source_item_entity.is_collection():
            msg = "Ignoring source part %s from non-collection %s"
            logging.info(msg, well, source_item_id)
            return source_item_entity

        source_part_entity = self.factory.get_part(
            collection=source_item_entity,
            well=well
        )

        return source_part_entity

    @staticmethod
    def _split_well_coordinate(well):
        pattern = r"([A-Z])([0-9]+)"
        match = re.match(pattern, well)
        if match:
            return coordinates_for(well)
        pattern = r"\[\[([0-9]+),[ \t]*([0-9]+)\]\]"
        match = re.match(pattern, well)
        if match:
            return (int(match[1]), int(match[2]))

    @staticmethod
    def _get_source_id(entry):
        if 'source' in entry:
            source = entry['source']
            if isinstance(source, list):
                return str(source[0]['id'])
            else:
                return str(entry['source'])
        return None

    @staticmethod
    def _get_routing_entry(routing_matrix, i, j):
        if not routing_matrix:
            return dict()
        entry = routing_matrix[i][j]
        if entry and isinstance(entry, Mapping):
            return entry
        return dict()

    @staticmethod
    def get_upload_matrix(entity):
        """
        Newer protocols have the key in all uppercase.
        """
        upload_attribute = entity.get_attribute('SAMPLE_UPLOADs')
        if upload_attribute:
            return upload_attribute['upload_matrix']

        upload_attribute = entity.get_attribute('SAMPLE_uploads')
        if not upload_attribute:
            return None

        upload_list = sorted(upload_attribute,
                             key=lambda upload: upload['upload_file_name'])

        upload_matrix = list()
        count = 0
        row = list()
        for upload in upload_list:
            row.append(upload['id'])
            count += 1
            if count % 12 == 0:
                upload_matrix.append(row)
                row = list()
        if count % 12 != 0:
            upload_matrix.append(row)

        return upload_matrix

    @staticmethod
    def get_routing_matrix(entity):
        routing_matrix = None
        if entity.has_attribute('routing_matrix'):
            logging.debug("%s %s has routing matrix",
                          entity.item_type, entity.item_id)
            routing_attribute = entity.get_attribute('routing_matrix')
            routing_matrix = routing_attribute['rows']
        elif entity.has_attribute('routing_dilution_matrix'):
            logging.debug("%s %s has routing dilution matrix",
                          entity.item_type, entity.item_id)
            routing_attribute = entity.get_attribute('routing_dilution_matrix')
            routing_matrix = routing_attribute['rows']
        elif entity.has_attribute('part_data'):
            logging.debug("%s %s has part_data",
                          entity.item_type, entity.item_id)
            routing_matrix = entity.get_attribute('part_data')
        return routing_matrix

    @staticmethod
    def _get_attributes(entry):
        if 'attributes' not in entry:
            return dict()
        return entry['attributes']
