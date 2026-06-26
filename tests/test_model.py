"""Unit tests for welpy.model: shared client-lookup queries."""

from welpy import model
from tests.helpers import (
    make_server, make_client, make_monitor, make_workspace)


def test_clients_in_filters():
    """clients_in returns only the clients assigned to the workspace."""
    ws1 = make_workspace(name="1")
    ws2 = make_workspace(name="2")
    a = make_client(workspace=ws1)
    b = make_client(workspace=ws2)
    c = make_client(workspace=ws1)
    server = make_server(clients=[a, b, c])

    assert model.clients_in(server, ws1) == [a, c]


def test_clients_visible_active():
    """clients_visible returns clients on the monitor's active workspace."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    inactive = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = active
    on_active = make_client(workspace=active)
    on_inactive = make_client(workspace=inactive)
    server = make_server(clients=[on_active, on_inactive])

    assert model.clients_visible(server, monitor) == [on_active]


def test_clients_visible_empty():
    """clients_visible returns [] for a monitor with no active workspace."""
    server = make_server()
    monitor = make_monitor()

    assert model.clients_visible(server, monitor) == []


def test_client_monitor_derives():
    """client_monitor reads the monitor through the client's workspace."""
    monitor = make_monitor()
    workspace = make_workspace(monitor=monitor)
    client = make_client(workspace=workspace)

    assert model.client_monitor(client) is monitor


def test_client_monitor_orphaned():
    """A client with no workspace has no monitor."""
    client = make_client(workspace=None)

    assert model.client_monitor(client) is None


def test_input_relay_constructs():
    """InputRelay starts with no bound method or grab."""
    relay = model.InputRelay(
        input_method=None, keyboard_grab=None,
        text_inputs=[], input_popups=[], anchor_for_surface=None,
        listeners=[], im_listeners=[], grab_listeners=[])

    assert relay.input_method is None
    assert not relay.text_inputs


def test_text_input_constructs():
    """TextInput wraps a wlroots text field with no pending enter target."""
    record = model.TextInput(
        input="WLR", pending_surface=None,
        pending_listeners=[], listeners=[])

    assert record.input == "WLR"
    assert record.pending_surface is None
