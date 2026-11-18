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


def _node(node_id, **props):
    return {'id': node_id, 'type': 'PipeWire:Interface:Node', 'info': {'props': props}}


def _link(link_id, out_id, in_id):
    return {'id': link_id, 'type': 'PipeWire:Interface:Link', 'info': {'output-node-id': out_id, 'input-node-id': in_id}}


def test_routes_into_the_phone_are_neomixers_links_only():
    objects = [
        _node(1, **{'node.name': pw_phone.SINK_NAME, 'media.class': 'Audio/Sink'}),
        _node(2, **{'node.name': 'inmedia', 'media.class': 'Audio/Sink'}),
        _node(3, **{'node.name': 'output.nm_route_x', 'media.class': 'Stream/Output/Audio', 'neomixer.target': 'neomixer_phone'}),
        _node(4, **{'node.name': 'spotify', 'media.class': 'Stream/Output/Audio'}),
        _node(5, **{'node.name': 'headset', 'media.class': 'Audio/Sink'}),
        _link(10, 2, 1), _link(11, 2, 1),  # a route, both channels
        _link(12, 3, 1),                   # a route with its own volume
        _link(13, 4, 1),                   # a program playing to the phone itself
        _link(14, 2, 5),                   # a route to another output
    ]
    assert pw_phone.routes_into_sink(objects) == [10, 11, 12]


def test_no_phone_no_routes():
    assert not pw_phone.routes_into_sink([_node(2, **{'node.name': 'inmedia'}), _link(10, 2, 3)])
