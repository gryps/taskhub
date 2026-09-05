from unittest.mock import Mock, patch

from app.taskhub import automatic_target_worker


def test_pipeline_worker_keeps_implementation_tasks() -> None:
    with patch("app.taskhub.least_loaded_worker") as least_loaded:
        worker = automatic_target_worker(Mock(), "code.change", "implementation-b")

    assert worker == "implementation-b"
    least_loaded.assert_not_called()


def test_workspace_quality_task_stays_on_pipeline_worker() -> None:
    with patch("app.taskhub.least_loaded_worker") as least_loaded:
        worker = automatic_target_worker(Mock(), "quality.env.check", "implementation-b")

    assert worker == "implementation-b"
    least_loaded.assert_not_called()


def test_model_review_uses_quality_pool() -> None:
    cursor = Mock()
    with patch("app.taskhub.least_loaded_worker", return_value="quality-worker") as least_loaded:
        worker = automatic_target_worker(cursor, "review.model", "implementation-b")

    assert worker == "quality-worker"
    least_loaded.assert_called_once_with(cursor, "review.model")


def test_gui_task_uses_gui_pool_instead_of_pipeline_worker() -> None:
    cursor = Mock()
    with patch("app.taskhub.least_loaded_worker", return_value="gui-worker") as least_loaded:
        worker = automatic_target_worker(cursor, "h5.inspect", "implementation-b")

    assert worker == "gui-worker"
    least_loaded.assert_called_once_with(cursor, "h5.inspect")


def test_general_pipeline_task_stays_on_pipeline_worker() -> None:
    with patch("app.taskhub.least_loaded_worker") as least_loaded:
        worker = automatic_target_worker(Mock(), "project.context.sync", "implementation-b")

    assert worker == "implementation-b"
    least_loaded.assert_not_called()
