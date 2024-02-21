from bs4 import BeautifulSoup
from typing import Union, List, Dict, Any
import requests
import logging
import distyll.loggerconfig
from typing import Union
from pathlib import Path
from openai import OpenAI
import yt_dlp
import os


def init_dl_dir(dir_path: Union[str, Path]) -> Path:
    """
    Initializes the download directory.

    Args:
        dir_path (Union[str, Path]): The path to the download directory.

    Returns:
        Path: The path to the initialized download directory.
    """
    if type(dir_path) is str:
        dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def get_openai_client(apikey: Union[str, None] = None) -> OpenAI:
    if apikey is None:
        oai_client = OpenAI(api_key=os.getenv("OPENAI_APIKEY"))
    else:
        oai_client = OpenAI(api_key=apikey)
    return oai_client


def get_arxiv_title(arxiv_url: str) -> Union[str, None]:
    """
    Helper function to get the title of an ArXiV paper
    :param arxiv_url:
    :return:
    """
    logging.info(f"Getting arXiV title from {arxiv_url}")
    response = requests.get(arxiv_url)
    if response.status_code != 200:
        logging.info(
            f"Failed to get the page. HTTP status code: {response.status_code}"
        )
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    title_element = soup.find("meta", {"name": "citation_title"})
    if title_element:
        return title_element["content"]
    else:
        logging.info("Failed to find the title element")
        return None


def chunk_text_by_num_words(
    source_text: str, max_chunk_words: int = 100, overlap_fraction: float = 0.25
) -> List[str]:
    """
    Chunk text input into a list of strings, using a number of words
    :param source_text: Input string to be chunked
    :param max_chunk_words: Maximum length of chunk, in words
    :param overlap_fraction: Overlap as a percentage of chunk_words. The overlap is prepended to each chunk.
    :return: return a list of words
    """
    logging.info(f"Chunking text of {len(source_text)} chars by number of words.")
    sep = " "
    overlap_words = int(max_chunk_words * overlap_fraction)

    source_text = source_text.strip()
    word_list = source_text.split(sep)
    chunks_list = list()

    n_chunks = ((len(word_list) - 1 + overlap_words) // max_chunk_words) + 1
    for i in range(n_chunks):
        window_words = word_list[
            max(max_chunk_words * i - overlap_words, 0) : max_chunk_words * (i + 1)
        ]
        chunks_list.append(sep.join(window_words))
    return chunks_list


# def chunk_text_by_num_chars(source_text: str, max_chunk_chars: int = 300, overlap_fraction: float = 0.25) -> List[str]:
#     """
#     Chunk text input into a list of strings
#     :param source_text: Input string to be chunked
#     :param max_chunk_chars: Maximum length of chunk, in words
#     :param overlap_fraction: Overlap as a percentage of chunk_words
#     :return: return a list of words
#     """
#     overlap_chars = int(max_chunk_chars * overlap_fraction)
#
#     source_text = source_text.strip()
#     chunks_list = list()
#
#     n_chunks = ((len(source_text) - 1 + overlap_chars) // max_chunk_chars) + 1
#     for i in range(n_chunks):
#         chunk = source_text[
#                 max(max_chunk_chars * i - overlap_chars, 0):
#                 max_chunk_chars * (i + 1)
#                 ]
#         chunks_list.append(chunk)
#     return chunks_list


def remove_multiple_whitespaces(source_text: str) -> str:
    """
    Replace multiple whitespaces with single space
    :param source_text:
    :return:
    """
    import re

    source_text = re.sub(r"\s+", " ", source_text)
    return source_text


def chunk_text(source_text: str) -> List[str]:
    """
    Chunk longer text
    :param source_text:
    :return:
    """
    logging.info(f"Chunking text of {len(source_text)} characters.")
    source_text = remove_multiple_whitespaces(source_text)
    return chunk_text_by_num_words(source_text)


def clean_yt_url(url: str) -> str:
    url = url.lower()
    url = url.split("?")[0]
    return url


def extract_metadata(video_info: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict()
    for k in ["title", "upload_date", "channel", "uploader"]:
        if k in video_info:
            metadata[k] = video_info[k]
    return metadata


def download_youtube(youtube_url: str, path_out: Path) -> str:
    """
    Download a YouTube video's audio and return its title
    :param youtube_url: URL of the YouTube video
    :param path_out: Path where the audio file will be downloaded
    :return: Video title
    """
    path_template = str(path_out.absolute())
    if path_template.endswith(".mp3"):
        path_template = path_template[:-4]

    yt_dlp_params = {
        "extract_audio": True,
        "format": "bestaudio/best",
        "audioformat": "mp3",
        "outtmpl": path_template,
        "quiet": True,
        "cachedir": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(yt_dlp_params) as video:
        result = video.extract_info(youtube_url, download=True)
        metadata = extract_metadata(result)
        video_title = result["title"]
        logging.info(f"Found {video_title} - downloading")
        video.download(youtube_url)
        logging.info(f"Successfully downloaded to {path_out}")

    return metadata


def get_youtube_metadata(youtube_url: str) -> Dict[str, str]:
    """
    Download a YouTube video and return its metadata
    :param youtube_url:
    :param path_out:
    :param audio_only:
    :return: Video title
    """
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "force_generic_extractor": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(youtube_url, download=False)
        metadata = extract_metadata(result)
    return metadata


def get_transcripts_from_audio_file(
    audio_file_path: Path,
    max_segment_len: int = 900,
    openai_apikey: Union[str, None] = None,
) -> List[str]:
    """
    Get transcripts of audio files using
    :param audio_file_path:
    :param max_segment_len:
    :param openai_apikey:
    :return:
    """
    oai_client = get_openai_client(openai_apikey)
    clip_outpaths = split_audio_files(audio_file_path, max_segment_len)
    transcript_texts = list()
    logging.info(f"Getting transcripts from {len(clip_outpaths)} audio files...")
    for i, clip_outpath in enumerate(clip_outpaths):
        logging.info(f"Processing transcript {i+1} of {len(clip_outpaths)}...")
        with clip_outpath.open("rb") as audio_file:
            transcript = oai_client.audio.transcriptions.create(
                model="whisper-1", file=audio_file
            )
            transcript_texts.append(transcript.text)

    # Clean up
    for clip_outpath in clip_outpaths:
        os.remove(clip_outpath)

    return transcript_texts


def split_audio_files(audio_file_path: Path, max_segment_len: int = 900) -> List[Path]:
    """
    Split long audio files
    (e.g. so that they fit within the allowed size for Whisper)
    :param audio_file_path:
    :param max_segment_len:
    :return: A list of file paths
    """
    from pydub import AudioSegment

    audio = AudioSegment.from_file(str(audio_file_path))
    logging.info(f"Splitting {audio_file_path} to chunks of {max_segment_len} seconds.")
    # Split long audio into segments
    clip_outpaths = list()
    if audio.duration_seconds > max_segment_len:
        n_segments = 1 + int(audio.duration_seconds) // max_segment_len
    else:
        n_segments = 1
    logging.info(f"Splitting audio to {n_segments}")
    for i in range(n_segments):
        start = max(0, (i * max_segment_len) - 5) * 1000
        end = ((i + 1) * max_segment_len) * 1000
        clip = audio[start:end]

        clip_outpath = audio_file_path.with_suffix(f".{i}.mp3")
        outfile = clip.export(str(clip_outpath))
        outfile.close()
        clip_outpaths.append(clip_outpath)
    return clip_outpaths