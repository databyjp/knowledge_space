from dataclasses import dataclass, fields, asdict
from enum import Enum
from typing import Optional, Union, Dict, List
import weaviate
from weaviate import Client
from weaviate.util import generate_uuid5
import openai
import os
from pathlib import Path

import preprocessing
import media
import logging

logger = logging.getLogger(__name__)

openai.api_key = os.environ["OPENAI_APIKEY"]

DEFAULT_CLASS_CONFIG = {
    "vectorizer": "text2vec-openai",
    "moduleConfig": {
        "generative-openai": {}
    },
}

TEMPDIR = Path("tempdata")
TEMPDIR.mkdir(exist_ok=True)


class CollectionName(Enum):
    CHUNK: str = "DataChunk"
    SOURCE: str = "DataSource"


@dataclass
class SourceData:
    path: str
    body: str
    title: Optional[str] = None


@dataclass
class ChunkData:
    source_title: Optional[str]
    source_path: str
    chunk_text: str
    chunk_number: int


def create_class_definition(collection_name, properties):
    """
    Create a object for a particular class
    :param collection_name:
    :param properties:
    :return:
    """
    return {
        "class": collection_name,
        "properties": properties,
        **DEFAULT_CLASS_CONFIG
    }


chunk_props = list()
for field in fields(ChunkData):
    if field.type == int:
        chunk_props.append({"name": field.name, "dataType": ["int"]})
    else:
        chunk_props.append({"name": field.name, "dataType": ["text"]})


# ===========================================================================
# DB MANAGEMENT
# ===========================================================================
def connect_weaviate(version: str = "latest") -> Client:
    """
    :param version: Weaviate version to use
    Instantiate Weaviate
    :return:
    """
    from weaviate import EmbeddedOptions

    # Replace this with other client instantiation method to connect to another instance of Weaviate
    client = weaviate.Client(
        embedded_options=EmbeddedOptions(version=version),
    )

    return client


def add_class_if_not_present(client: Client, collection_config: Dict) -> Union[bool, None]:
    """
    Add a Weaviate class if one does not exist
    :param client:
    :param collection_config:
    :return:
    """
    if not client.schema.exists(collection_config['class']):
        logger.info(f"Creating a new class: {collection_config['class']}")
        client.schema.create_class(collection_config)
        return True
    else:
        logger.info(f"Found {collection_config['class']} in the schema. Skipping class creation")
        return None


# ===========================================================================
# Collection
# ===========================================================================
class DBConnection:

    def __init__(
            self,
            client: Union[Client, None] = None,
            source_class: str = CollectionName.SOURCE.value,
            chunk_class: str = CollectionName.CHUNK.value
    ):
        if client is None:
            client = connect_weaviate()
        self.client = client

        DB_CLASSES = {
            "classes": [
                create_class_definition(
                    source_class,
                    [{"name": field.name, "dataType": ["text"]} for field in fields(SourceData)]
                ),
                create_class_definition(
                    chunk_class,
                    chunk_props
                ),
            ]
        }

        for c in DB_CLASSES["classes"]:
            add_class_if_not_present(client, c)

        self.source_class = source_class
        self.chunk_class = chunk_class

    def _add_object(self, data_object, collection_name):
        """
        Add an object to the collection
        :param data_object:
        :param collection_name:
        :return:
        """
        uuid = generate_uuid5(data_object)
        if self.client.data_object.exists(uuid=uuid, class_name=collection_name):
            return None
        else:
            self.client.data_object.create(
                data_object=data_object,
                class_name=collection_name,
                uuid=generate_uuid5(data_object)
            )
            return True

    def import_chunks(self, chunks: List[str], source_object_data: SourceData, chunk_number_offset: int = 0):
        """
        Import text chunks via batch import process
        :param chunks:
        :param source_object_data:
        :param chunk_number_offset:
        :return:
        """
        counter = 0
        self.client.batch.configure(batch_size=100)
        with self.client.batch as batch:
            for i, chunk_text in enumerate(chunks):
                chunk_object = ChunkData(
                    source_path=source_object_data.path,
                    source_title=source_object_data.title,
                    chunk_text=chunk_text,
                    chunk_number=i+chunk_number_offset
                )
                batch.add_data_object(
                    class_name=self.chunk_class,
                    data_object=asdict(chunk_object),
                    uuid=generate_uuid5(asdict(chunk_object))
                )
                counter += 1
        return counter

    def add_data(
            self, source_object_data: SourceData, chunk_number_offset: int = 0
    ) -> int:
        """
        Add objects to Weaviate
        :param source_object_data: Source data
        :param chunk_number_offset: Any offset to chunk number
        :return:
        """
        chunks = preprocessing.chunk_text(source_object_data.body)
        # TODO - add source object import
        counter = self.import_chunks(chunks, source_object_data, chunk_number_offset)

        return counter

    def add_text(
            self, source_path: str, source_text: str,
            source_title: Optional[str] = None, chunk_number_offset: int = 0
    ):
        """
        Add data from text input
        :param source_path:
        :param source_text:
        :param source_title:
        :param chunk_number_offset:
        :return:
        """
        source_object_data = SourceData(
            path=source_path,
            body=source_text,
            title=source_title,
        )
        return self.add_data(source_object_data, chunk_number_offset=chunk_number_offset)

    def add_from_youtube(self, youtube_url: str) -> int:
        """
        Add the transcript of a YouTube video to Weaviate
        :param youtube_url:
        :return:
        """
        # Grab the YouTube Video & convert to transcript text
        tmp_outpath = TEMPDIR/'temp_audio.mp3'
        video_title = media.download_youtube(youtube_url=youtube_url, path_out=tmp_outpath)
        transcript_texts = media.get_transcripts_from_audio_file(tmp_outpath)

        # Ingest transcripts into the database
        obj_count = 0
        for transcript_text in transcript_texts:
            obj_count += self.add_text(
                source_path=youtube_url, source_text=transcript_text,
                chunk_number_offset=obj_count, source_title=video_title
            )

        # Cleanup - if original file still exists
        if os.path.exists(tmp_outpath):
            os.remove(tmp_outpath)

        return obj_count

    # def add_pdf(self, pdf_url: str) -> int:
    #     """
    #     Add a PDF to the database
    #     :param pdf_url:
    #     :return:
    #     """
    #     text_content = utils.download_and_parse_pdf(pdf_url)
    #     return self.add_text(
    #         source_path=pdf_url,
    #         source_text=text_content,
    #         source_title=pdf_url
    #     )
    #