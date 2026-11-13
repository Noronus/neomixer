from neomixer.scripts import pw_phone


def test_the_config_plays_to_the_phone_and_listens_for_its_microphone():
    config = pw_phone.build_config('10.0.0.7', 40000, 47801, 'Pixel 10')

    assert 'destination.ip = "10.0.0.7"' in config
    assert 'destination.port = 40000' in config
    assert 'source.port = 47801' in config
    assert f'node.name = "{pw_phone.SINK_NAME}"' in config
    assert f'node.name = "{pw_phone.SOURCE_NAME}"' in config
    assert 'node.description = "Pixel 10"' in config
    assert 'node.description = "Pixel 10 microphone"' in config


def test_a_phone_cannot_write_its_own_config_with_its_name():
    config = pw_phone.build_config('10.0.0.7', 40000, 47801, 'x" } ] context.exec = [ { path = "/bin/sh"')

    described = [line for line in config.splitlines() if 'node.description' in line]
    assert len(described) == 2
    for line in described:
        # one pair of quotes, and nothing in between that could end the value or start another
        value = line.split('=', 1)[1]
        assert value.count('"') == 2 and not set('{}[]=') & set(value)


def test_a_name_with_nothing_usable_in_it_still_names_the_device():
    assert 'node.description = "Phone"' in pw_phone.build_config('10.0.0.7', 40000, 47801, '"{}"')


def test_the_microphone_buffer_is_a_multiple_of_a_packet():
    '''The RTP source asks for that, it warns and rounds otherwise.'''
    assert pw_phone.MIC_LATENCY_MS % pw_phone.PACKET_MS == 0
