from pydantic import BaseModel


class KnownApp(BaseModel):
    '''
    What NeoMixer remembers about a program, saved in the config so that it outlives the
    program and reboots.
        "key" is the program's identity, see utils.app_identity.app_key
        "device" is where its streams go: None means no choice was made and the session
            manager decides, '' means follow the default, and otherwise it is the device name
        "volume" and "mute" are only applied once they were changed, None leaves them alone
    '''
    key: str
    label: str
    icon: str | None = None
    binary: str | None = None
    device: str | None = None
    volume: int | None = None
    mute: bool | None = None
    last_seen: float = 0
