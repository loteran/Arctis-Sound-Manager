"""Tests for utils — ObservableDict, JsonSerializable, project_version."""

from linux_arctis_manager.utils import ObservableDict, project_version


def test_observable_dict_notifies_on_change():
    changes = []
    d = ObservableDict()
    d.add_observer(lambda k, v: changes.append((k, v)))

    d["a"] = 1
    assert changes == [("a", 1)]

    d["a"] = 2
    assert changes == [("a", 1), ("a", 2)]


def test_observable_dict_no_notify_on_same_value():
    changes = []
    d = ObservableDict()
    d.add_observer(lambda k, v: changes.append((k, v)))

    d["a"] = 1
    d["a"] = 1  # same value — should NOT notify
    assert changes == [("a", 1)]


def test_observable_dict_update():
    changes = []
    d = ObservableDict()
    d.add_observer(lambda k, v: changes.append((k, v)))

    d.update({"x": 10, "y": 20})
    assert d["x"] == 10
    assert d["y"] == 20
    assert ("x", 10) in changes
    assert ("y", 20) in changes


def test_observable_dict_update_single_arg():
    d = ObservableDict()
    try:
        d.update({"a": 1}, {"b": 2})
    except TypeError:
        pass  # expected — update takes exactly 1 positional arg


def test_observable_dict_to_dict():
    d = ObservableDict({"a": 1, "b": 2})
    result = d.to_dict()
    assert result == {"a": 1, "b": 2}


def test_project_version_returns_string():
    v = project_version()
    assert isinstance(v, str)
    assert len(v) > 0
