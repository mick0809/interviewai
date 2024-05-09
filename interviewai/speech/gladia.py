import asyncio
import websockets
import json
import base64
import pyaudio

# Constants for audio recording
FORMAT = pyaudio.paInt16  # Audio format (16-bit PCM in this example)
CHANNELS = 1  # Number of audio channels
RATE = 16000  # Sampling rate (16000 samples per second)
CHUNK = 1024  # Number of frames per buffer

gladiaKey = 'your key'
gladiaUrl = "wss://api.gladia.io/audio/text/audio-transcription"

ERROR_KEY = 'error'
TYPE_KEY = 'type'
TRANSCRIPTION_KEY = 'transcription'
LANGUAGE_KEY = 'language'


async def send_audio(socket, stream):
    configuration = {
        "x_gladia_key": gladiaKey,
        # "language_behaviour":'automatic multiple languages',
        "endpointing": 10,

    }
    await socket.send(json.dumps(configuration))

    try:
        while True:
            # Read a chunk of audio data from the microphone
            data = stream.read(CHUNK)
            # Encode the chunk to base64
            base64_data = base64.b64encode(data).decode('utf-8')

            message = {
                'frames': base64_data
            }
            # Send the encoded chunk to the WebSocket
            await socket.send(json.dumps(message))
            await asyncio.sleep(0.1)  # Small delay to prevent flooding the server
    except asyncio.CancelledError:
        # Handle task cancellation here if necessary
        pass


async def receive_transcription(socket):
    while True:
        response = await socket.recv()
        utterance = json.loads(response)
        if utterance:
            if ERROR_KEY in utterance:
                print(f"{utterance[ERROR_KEY]}")
                break
            elif TRANSCRIPTION_KEY in utterance and utterance[TYPE_KEY] == 'final':
                print(f"{utterance[TYPE_KEY]}: ({utterance[LANGUAGE_KEY]}) {utterance[TRANSCRIPTION_KEY]}")
        else:
            print('empty,waiting for next utterance...')


async def main():
    # Set up audio stream
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

    async with websockets.connect(gladiaUrl) as socket:
        send_task = asyncio.create_task(send_audio(socket, stream))
        receive_task = asyncio.create_task(receive_transcription(socket))
        await asyncio.gather(send_task, receive_task)

    # Clean up PyAudio stream
    stream.stop_stream()
    stream.close()
    p.terminate()


asyncio.run(main())
