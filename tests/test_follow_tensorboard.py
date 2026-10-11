from helpers import tf_event, tf_record, tf_version_event
from runboard.follow import tensorboard
from runboard.follow import Record
from runboard.follow.tensorboard import TensorBoardFollower, crc32c


def test_crc32c_known_vector():
    assert crc32c(b"123456789") == 0xE3069283


def test_reads_simple_and_tensor_scalars(tmp_path):
    path = tmp_path / "events.out.tfevents.1.host"
    path.write_bytes(
        tf_record(tf_version_event())
        + tf_record(tf_event(5, 100.0, scalars=[("train/loss", 0.5)]))
        + tf_record(tf_event(6, 101.0, tensors=[("eval/acc", 0.25)]))
    )
    assert TensorBoardFollower(path).poll() == [Record(5, 100.0, {"train/loss": 0.5}), Record(6, 101.0, {"eval/acc": 0.25})]


def test_waits_for_partial_record_and_skips_bad_checksum(tmp_path):
    path = tmp_path / "events.out.tfevents.2.host"
    good = tf_record(tf_event(1, 1.0, scalars=[("loss", 0.5)]))
    bad = bytearray(tf_record(tf_event(2, 2.0, scalars=[("loss", 0.25)])))
    bad[-1] ^= 0xFF
    last = tf_record(tf_event(3, 3.0, scalars=[("loss", 0.125)]))
    path.write_bytes(good + bytes(bad) + last[:10])
    follower = TensorBoardFollower(path)
    assert follower.poll() == [Record(1, 1.0, {"loss": 0.5})]
    with open(path, "ab") as f:
        f.write(last[10:])
    assert follower.poll() == [Record(3, 3.0, {"loss": 0.125})]


def test_skips_records_larger_than_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(tensorboard, "MAX_RECORD", 64)
    path = tmp_path / "events.out.tfevents.3.host"
    path.write_bytes(tf_record(bytes(200)) + tf_record(tf_event(4, 4.0, scalars=[("loss", 0.5)])))
    assert TensorBoardFollower(path).poll() == [Record(4, 4.0, {"loss": 0.5})]


def test_resumes_from_saved_offset(tmp_path):
    path = tmp_path / "events.out.tfevents.4.host"
    path.write_bytes(tf_record(tf_event(1, 1.0, scalars=[("loss", 0.5)])))
    first = TensorBoardFollower(path)
    first.poll()
    with open(path, "ab") as f:
        f.write(tf_record(tf_event(2, 2.0, scalars=[("loss", 0.25)])))
    assert TensorBoardFollower(path, first.state()).poll() == [Record(2, 2.0, {"loss": 0.25})]


def test_omitted_step_means_step_zero(tmp_path):
    path = tmp_path / "events.out.tfevents.5.host"
    path.write_bytes(tf_record(tf_event(0, 1.0, scalars=[("loss", 0.5)])))
    assert TensorBoardFollower(path).poll() == [Record(0, 1.0, {"loss": 0.5})]


def test_add_scalars_directories_name_each_series(tmp_path):
    torch_dir = tmp_path / "logs" / "loss_train"
    tbx_dir = tmp_path / "logs" / "eval" / "acc"
    plain_dir = tmp_path / "runs" / "exp"
    for directory in (torch_dir, tbx_dir, plain_dir):
        directory.mkdir(parents=True)
    (torch_dir / "events.out.tfevents.1.h").write_bytes(tf_record(tf_event(1, 1.0, scalars=[("loss", 0.5)])))
    (tbx_dir / "events.out.tfevents.1.h").write_bytes(tf_record(tf_event(1, 1.0, scalars=[("eval", 0.25)])))
    (plain_dir / "events.out.tfevents.1.h").write_bytes(tf_record(tf_event(1, 1.0, scalars=[("train/loss", 0.125)])))
    assert TensorBoardFollower(torch_dir / "events.out.tfevents.1.h").poll() == [Record(1, 1.0, {"loss/train": 0.5})]
    assert TensorBoardFollower(tbx_dir / "events.out.tfevents.1.h").poll() == [Record(1, 1.0, {"eval/acc": 0.25})]
    assert TensorBoardFollower(plain_dir / "events.out.tfevents.1.h").poll() == [Record(1, 1.0, {"train/loss": 0.125})]


def test_only_scalar_events_pay_for_data_checksums(tmp_path, monkeypatch):
    path = tmp_path / "events.out.tfevents.6.host"
    path.write_bytes(tf_record(bytes(500)) + tf_record(tf_event(2, 2.0, scalars=[("loss", 0.5)])))
    sizes = []
    original = tensorboard.crc32c
    monkeypatch.setattr(tensorboard, "crc32c", lambda data: sizes.append(len(data)) or original(data))
    assert TensorBoardFollower(path).poll() == [Record(2, 2.0, {"loss": 0.5})]
    assert max(sizes) < 500


def test_poll_stops_at_the_byte_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(tensorboard, "MAX_BYTES_PER_POLL", 1)
    path = tmp_path / "events.out.tfevents.7.host"
    path.write_bytes(tf_record(tf_event(1, 1.0, scalars=[("loss", 0.5)])) + tf_record(tf_event(2, 2.0, scalars=[("loss", 0.25)])))
    follower = TensorBoardFollower(path)
    assert follower.poll() == [Record(1, 1.0, {"loss": 0.5})]
    assert follower.poll() == [Record(2, 2.0, {"loss": 0.25})]
