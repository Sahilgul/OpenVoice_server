import os
import time
import torch
from melo.api import TTS
import se_extractor
import io
import magic
import logging
 
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from typing import Optional
from pydantic import BaseModel
from api import ToneColorConverter

logging.basicConfig(level=logging.INFO)

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# New checkpoint paths
ckpt_base = 'checkpoints_v2/base_speakers/ses'

device = "cuda:0" if torch.cuda.is_available() else "cpu"

tone_color_converter = ToneColorConverter('checkpoints_v2/converter/config.json', device=device)
tone_color_converter.load_ckpt('checkpoints_v2/converter/checkpoint.pth')

output_dir = 'outputs'
os.makedirs(output_dir, exist_ok=True)

# Available base speakers
base_speakers = ['en-au', 'en-br', 'en-default', 'en-india', 'en-newest', 'en-us', 'es', 'fr', 'jp', 'kr', 'zh']
key_map = {'en-newest': ('EN-Newest', 'EN_NEWEST'),
           'en-us': ('EN-US', 'EN'),
           'en-br': ('EN-BR', 'EN'),
           'en-india': ('EN_INDIA', 'EN'),
           'en-au': ('EN-AU', 'EN'),
           'en-default': ('EN-Default', 'EN'),
           'es': ('ES', 'ES'),
           'fr': ('FR', 'FR'),
           'jp': ('JP', 'JP'),
           'kr': ('KR', 'KR'),
           'zh': ('ZH', 'ZH')
           }

source_se = {
    "en-newest": torch.load(f'{ckpt_base}/en-newest.pth').to(device),
    "en-us": torch.load(f'{ckpt_base}/en-us.pth').to(device),
    "en-br": torch.load(f'{ckpt_base}/en-br.pth').to(device),
    "en-india": torch.load(f'{ckpt_base}/en-india.pth').to(device),
    "en-au": torch.load(f'{ckpt_base}/en-au.pth').to(device),
    "en-default": torch.load(f'{ckpt_base}/en-default.pth').to(device),
    "es": torch.load(f'{ckpt_base}/es.pth').to(device),
    "fr": torch.load(f'{ckpt_base}/fr.pth').to(device),
    "jp": torch.load(f'{ckpt_base}/jp.pth').to(device),
    "kr": torch.load(f'{ckpt_base}/kr.pth').to(device),
    "zh": torch.load(f'{ckpt_base}/zh.pth').to(device)
}
logging.info('Loaded base speakers.')
logging.info('Loading TTS models...')
model = {}

# When running on CPU, only load the en-newest model
if device == "cpu":
    base_speakers = ['en-newest']

for accent in base_speakers:
    logging.info(f'Loading {accent}...')
    model[accent] = TTS(language=key_map[accent][1], device=device)
    logging.info('...done.')

logging.info('Loaded TTS models.')


class UploadAudioRequest(BaseModel):
    audio_file_label: str


@app.on_event("startup")
async def startup_event():
    test_text = "This is a test sentence generated by the OpenVoice API."
    voice = "demo_speaker0"
    await synthesize_speech(test_text, voice)


@app.get("/base_tts/")
async def base_tts(text: str, accent: Optional[str] = 'en-newest', speed: Optional[float] = 1.0):
    """
    Perform text-to-speech conversion using only the base speaker.

    :param text: The text to be converted to speech.
    :type text: str
    :param accent: The accent to be used for the synthesized speech, defaults to 'en-newest'.
    :type accent: str, optional
    :param speed: The speed of the synthesized speech, defaults to 1.0.
    :type speed: float, optional
    :return: The speech audio.
    :rtype: .wav file
    """
    global model

    if accent not in model:
        logging.info(f'Loading {accent}...')
        model[accent] = TTS(language=key_map[accent][1], device=device)
        logging.info('...done.')

    try:
        save_path = f'{output_dir}/output_v2_{accent}.wav'
        model[accent].tts_to_file(text, model[accent].hps.data.spk2id[key_map[accent][0]], save_path, speed=speed)
        result = StreamingResponse(open(save_path, 'rb'), media_type="audio/wav")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/change_voice/")
async def change_voice(reference_speaker: str = Form(...), file: UploadFile = File(...), watermark: Optional[str] = "@MyShell"):
    """
    Change the voice of an existing audio file.

    :param reference_speaker: The name of the reference speaker.
    :type reference_speaker: str
    :param file: The audio file to be changed.
    :type file: UploadFile
    :param watermark: The watermark to be encoded in the voice conversion, defaults to '@MyShell'.
    :type watermark: str, optional
    :return: The audio file with the changed voice.
    :rtype: .wav file
    """
    try:
        logging.info(f'changing voice to {reference_speaker}...')

        if watermark:
            logging.info(f'watermark: {watermark}')

        contents = await file.read()
        temp_file = io.BytesIO(contents)
        matching_files = [file for file in os.listdir("resources") if file.startswith(str(reference_speaker))]
        if not matching_files:
            raise HTTPException(status_code=400, detail="No matching reference speaker found.")
        reference_speaker_file = f'resources/{matching_files[0]}'
        target_se, audio_name = se_extractor.get_se(reference_speaker_file, tone_color_converter, target_dir='processed', vad=True)
        save_path = f'{output_dir}/output_v2_{reference_speaker}.wav'
        tone_color_converter.convert(
            audio_src_path=temp_file,
            src_se=source_se['en-newest'],
            tgt_se=target_se,
            output_path=save_path,
            message=watermark)
        result = StreamingResponse(open(save_path, 'rb'), media_type="audio/wav")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload_audio/")
async def upload_audio(audio_file_label: str = Form(...), file: UploadFile = File(...)):
    """
    Upload an audio file for later use as the reference audio.

    :param audio_file_label: The label for the audio file.
    :param file: The audio file to be uploaded.
    :type file: UploadFile
    :return: Confirmation of successful upload.
    :rtype: dict
    """
    try:
        contents = await file.read()

        allowed_extensions = {'wav', 'mp3', 'flac', 'ogg'}
        max_file_size = 5 * 1024 * 1024  # 5MB

        if not file.filename.split('.')[-1] in allowed_extensions:
            return {"error": "Invalid file type. Allowed types are: wav, mp3, flac, ogg"}

        if len(contents) > max_file_size:
            return {"error": "File size is over limit. Max size is 5MB."}

        # Note: we need to first write the file in order to check magic.
        temp_file = io.BytesIO(contents)
        file_format = magic.from_buffer(temp_file.read(), mime=True)

        if 'audio' not in file_format:
            return {"error": "Invalid file content."}

        # Make sure the resources directory exists
        os.makedirs("resources", exist_ok=True)

        # Use provided 'audio_file_label' for stored file's name.
        # We retain the file extension to ensure appropriate processing later.
        file_extension = file.filename.split('.')[-1]
        stored_file_name = f"{audio_file_label}.{file_extension}"

        with open(f"resources/{stored_file_name}", "wb") as f:
            f.write(contents)

        return {"message": f"File {file.filename} uploaded successfully with label {audio_file_label}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/synthesize_speech/")
async def synthesize_speech(
        text: str,
        voice: str,
        accent: Optional[str] = 'en-newest',
        speed: Optional[float] = 1.0,
        watermark: Optional[str] = "@MyShell"
):
    """
    Synthesize speech from text using a specified voice and style.

    :param text: The text to be synthesized into speech.
    :type text: str
    :param voice: The voice to be used for the synthesized speech.
    :type voice: str
    :param accent: The accent to be used for the synthesized speech, defaults to 'en-newest'.
    :type accent: str, optional
    :param speed: The speed of the synthesized speech, defaults to 1.0.
    :type speed: float, optional
    :param watermark: The watermark to be encoded in the voice conversion, defaults to '@MyShell'.
    :type watermark: str, optional
    :return: The synthesized speech as a .wav file.
    :rtype: .wav file
    """
    global model

    if accent not in model:
        logging.info(f'Loading {accent}...')
        model[accent] = TTS(language=key_map[accent][1], device=device)
        logging.info('...done.')

    start_time = time.time()
    try:
        logging.info(f'Generating speech for {voice}')
        if watermark:
            logging.info(f'watermark: {watermark}')

        # Retrieve the correct file based on the 'voice' parameter
        # It should match the 'audio_file_label' used while uploading
        matching_files = [file for file in os.listdir("resources") if file.startswith(voice)]

        if not matching_files:
            raise HTTPException(status_code=400, detail="No matching voice found.")

        reference_speaker = f'resources/{matching_files[0]}'

        target_se, audio_name = se_extractor.get_se(reference_speaker, tone_color_converter, target_dir='processed', vad=True)

        # Run the base speaker tts
        src_path = f'{output_dir}/tmp.wav'
        save_path = f'{output_dir}/output_v2_{accent}.wav'
        model[accent].tts_to_file(text, model[accent].hps.data.spk2id[key_map[accent][0]], src_path, speed=speed)

        # Run the tone color converter
        tone_color_converter.convert(
            audio_src_path=src_path,
            src_se=source_se[accent],
            tgt_se=target_se,
            output_path=save_path,
            message=watermark)

        result = StreamingResponse(open(save_path, 'rb'), media_type="audio/wav")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    end_time = time.time()
    elapsed_time = end_time - start_time

    result.headers["X-Elapsed-Time"] = str(elapsed_time)
    result.headers["X-Device-Used"] = device

    # Add CORS headers
    result.headers["Access-Control-Allow-Origin"] = "*"  # Required for CORS support
    result.headers["Access-Control-Allow-Credentials"] = "true"  # Required for cookies, authorization headers with HTTPS
    result.headers["Access-Control-Allow-Headers"] = "Origin, Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token, locale"
    result.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"

    return result