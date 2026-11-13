from pydantic import BaseModel


class RemotePhone(BaseModel):
    '''
    A phone that was allowed to control the mixer.
        "name" is what the phone calls itself, to tell it apart in the list
        "secret" is what it proves itself with on every connection, as hex
    '''
    name: str
    secret: str
