from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib import parse, request

import capture
import cv2
import numpy as np


@dataclass(frozen=True)
class TelegramAction:
    text: str
    value: str


@dataclass(frozen=True)
class NotificationResult:
    source: Literal[
        "console", "telegram", "space", "timeout"
    ]  # "console", "telegram", or "timeout"
    action_value: str | None = None
    callback_query_id: str | None = None
    raw_update: dict[str, Any] | None = None


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        chat_id: int | str,
        *,
        api_base: str | None = None,
        request_timeout: float = 35.0,
        receive: bool = True,
    ) -> None:
        # When False, nothing is read back from Telegram: send_and_wait neither
        # sends the question nor polls for an answer (console / space / timeout
        # only), bot commands and the "Fechar" button are skipped.  Plain sends
        # (send_message, send_photo, ...) still go out.  Can be flipped at runtime.
        self.receive = receive
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.api_base = api_base or f"https://api.telegram.org/bot{bot_token}"
        self.request_timeout = request_timeout
        self._offset: int | None = None
        self._offset_lock = threading.Lock()

        # Command registry
        self._commands: dict[str, Any] = {}
        self._commands_lock = threading.Lock()

        # Background dispatcher
        self._dispatcher_thread: threading.Thread | None = None
        self._dispatcher_stop = threading.Event()
        self._update_sinks: list = []
        self._update_sinks_lock = threading.Lock()

        # Set when the app is shutting down — prevents close-button handlers
        # from deleting messages on exit.
        self._app_stopping = threading.Event()

    def stop(self):
        self._app_stopping.set()
        self._dispatcher_stop.set()
        if self._dispatcher_thread is not None:
            self._dispatcher_thread.join(timeout=0)
        with self._commands_lock:
            self._commands.clear()
        if not self.receive:
            return
        try:
            self._post_json("deleteMyCommands", {})
        except Exception:
            pass

    def send_message(
        self,
        text: str,
        *,
        actions: list[TelegramAction] | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        closer: bool = False,
        timeout_seconds: float | None = None,
    ) -> None:
        """Non-blocking: dispatches the send to a background thread."""
        threading.Thread(
            target=self._send_message,
            kwargs=dict(
                text=text,
                actions=actions,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                closer=closer,
                timeout_seconds=timeout_seconds,
            ),
            daemon=True,
        ).start()

    def _send_message(
        self,
        text: str,
        *,
        actions: list[TelegramAction] | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        closer: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if closer and self.receive:
            if actions is None:
                actions = []
            actions.append(TelegramAction("Fechar", "close_auto"))

        reply_markup = self._build_reply_markup(actions)
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)

        response = self._post_json("sendMessage", payload)

        if closer and self.receive:
            try:
                self._start_close_listener(response["result"]["message_id"])
            except Exception:
                pass

        if timeout_seconds is not None:
            try:
                self._start_timeout_deleter(
                    response["result"]["message_id"], timeout_seconds
                )
            except Exception:
                pass

        return response

    def send_photo(
        self,
        *,
        image_path: str | None = None,
        frame: np.ndarray | None = None,
        caption: str | None = None,
        actions: list[TelegramAction] | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        image_format: str = ".jpg",
        jpeg_quality: int = 95,
        png_compression: int = 3,
        filename: str | None = None,
        closer: bool = False,
        timeout_seconds: float | None = None,
    ) -> None:
        """Non-blocking: dispatches the send to a background thread."""
        threading.Thread(
            target=self._send_photo,
            kwargs=dict(
                image_path=image_path,
                frame=frame,
                caption=caption,
                actions=actions,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                image_format=image_format,
                jpeg_quality=jpeg_quality,
                png_compression=png_compression,
                filename=filename,
                closer=closer,
                timeout_seconds=timeout_seconds,
            ),
            daemon=True,
        ).start()

    def _send_photo(
        self,
        *,
        image_path: str | None = None,
        frame: np.ndarray | None = None,
        caption: str | None = None,
        actions: list[TelegramAction] | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        image_format: str = ".jpg",
        jpeg_quality: int = 95,
        png_compression: int = 3,
        filename: str | None = None,
        closer: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if (image_path is None) == (frame is None):
            raise ValueError("Provide exactly one of image_path or frame.")

        fields: dict[str, str] = {
            "chat_id": self.chat_id,
            "disable_notification": json.dumps(disable_notification),
        }

        if caption:
            fields["caption"] = caption
        if parse_mode:
            fields["parse_mode"] = parse_mode
        if closer and self.receive:
            if actions is None:
                actions = []
            actions.append(TelegramAction("Fechar", "close_auto"))

        reply_markup = self._build_reply_markup(actions)
        if reply_markup is not None:
            fields["reply_markup"] = json.dumps(reply_markup)

        if image_path is not None:
            if not os.path.isfile(image_path):
                raise FileNotFoundError(f"Image not found: {image_path}")

            response = self._post_multipart(
                "sendPhoto",
                fields=fields,
                files={
                    "photo": {
                        "filename": os.path.basename(image_path),
                        "content_type": mimetypes.guess_type(image_path)[0]
                        or "application/octet-stream",
                        "data": self._read_file_bytes(image_path),
                    }
                },
            )
        else:
            encoded_bytes, used_filename, content_type = (
                self._encode_frame_for_telegram(
                    frame=frame,  # type: ignore
                    image_format=image_format,
                    jpeg_quality=jpeg_quality,
                    png_compression=png_compression,
                    filename=filename,
                )
            )

            response = self._post_multipart(
                "sendPhoto",
                fields=fields,
                files={
                    "photo": {
                        "filename": used_filename,
                        "content_type": content_type,
                        "data": encoded_bytes,
                    }
                },
            )

        if closer and self.receive:
            try:
                self._start_close_listener(response["result"]["message_id"])
            except Exception:
                pass

        if timeout_seconds is not None:
            try:
                self._start_timeout_deleter(
                    response["result"]["message_id"], timeout_seconds
                )
            except Exception:
                pass

        return response

    def send_notification(
        self,
        *,
        text: str | None = None,
        image_path: str | None = None,
        frame: np.ndarray | None = None,
        actions: list[TelegramAction] | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        closer: bool = False,
        timeout_seconds: float | None = None,
    ) -> None:
        """Non-blocking: dispatches the send to a background thread."""
        threading.Thread(
            target=self._send_notification,
            kwargs=dict(
                text=text,
                image_path=image_path,
                frame=frame,
                actions=actions,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                closer=closer,
                timeout_seconds=timeout_seconds,
            ),
            daemon=True,
        ).start()

    def _send_notification(
        self,
        *,
        text: str | None = None,
        image_path: str | None = None,
        frame: np.ndarray | None = None,
        actions: list[TelegramAction] | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        closer: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if image_path is not None or frame is not None:
            response = self._send_photo(
                image_path=image_path,
                frame=frame,
                caption=text,
                actions=actions,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                closer=closer,
            )
        else:
            if text is None:
                raise ValueError("Either text, image_path, or frame must be provided.")
            response = self._send_message(
                text=text,
                actions=actions,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                closer=closer,
            )

        if timeout_seconds is not None:
            try:
                self._start_timeout_deleter(
                    response["result"]["message_id"], timeout_seconds
                )
            except Exception:
                pass

        return response

    def flush_input(self):
        import msvcrt

        while msvcrt.kbhit():
            msvcrt.getch()

    def send_and_wait(
        self,
        *,
        text: str | None = None,
        image_path: str | None = None,
        frame: np.ndarray | None = None,
        actions: list[TelegramAction] | None = None,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        console_prompt: str = "Press Enter to continue...",
        timeout_seconds: float | None = None,
        delete_message_after_finish: bool = True,
        wait_for_text: bool = False,
        catch_space: bool = False,
    ) -> NotificationResult:
        allowed_updates = (
            ["callback_query", "message"] if wait_for_text else ["callback_query"]
        )

        token_prefix = f"act:{uuid.uuid4().hex}:"
        message_id: int | None = None

        if self.receive:
            # Only flush stale updates when the dispatcher is not already keeping
            # the offset current; when it is running, the offset is always fresh.
            if not self._dispatcher_is_running():
                self._sync_offset(allowed_updates=allowed_updates)

            tagged_actions = [
                TelegramAction(text=a.text, value=token_prefix + a.value)
                for a in (actions or [])
            ]

            sent = self._send_notification(
                text=text,
                image_path=image_path,
                frame=frame,
                actions=tagged_actions,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
            )
            message_id = sent["result"]["message_id"]
        else:
            # The question would only have been on Telegram — show it here.
            if text:
                print(text)
            if actions:
                print("Opções: " + ", ".join(a.value for a in actions))

        response_message_id: list[int | None] = [None]

        finished = threading.Event()
        result_box: dict[str, NotificationResult] = {}
        question_deleted = threading.Event()

        def do_delete_question() -> None:
            if not delete_message_after_finish or message_id is None:
                return
            if question_deleted.is_set():
                return
            question_deleted.set()
            try:
                self.delete_message(message_id=message_id)
            except Exception:
                pass

        def finish(result: NotificationResult) -> None:
            if finished.is_set():
                return
            result_box["result"] = result
            finished.set()
            if result.source in ("console", "telegram", "space"):
                do_delete_question()
                if wait_for_text and response_message_id[0] is not None:
                    try:
                        self.delete_message(message_id=response_message_id[0])
                    except Exception:
                        pass

        def wait_for_console() -> None:
            try:
                import sys

                self.flush_input()
                typed = input(console_prompt)
                finish(
                    NotificationResult(
                        source="console",
                        action_value=typed,
                    )
                )
            except EOFError:
                pass
            except Exception as exc:
                finish(
                    NotificationResult(
                        source="console",
                        raw_update={"error": f"Console input failed: {exc}"},
                    )
                )

        console_thread = threading.Thread(target=wait_for_console, daemon=True)
        console_thread.start()

        def on_space() -> None:
            finish(NotificationResult(source="space"))

        def on_capture_exit() -> None:
            do_delete_question()
            if not finished.is_set():
                finished.set()
                result_box.setdefault("result", NotificationResult(source="timeout"))

        _capture_mod = None
        try:
            import capture as _cm  # type: ignore

            if catch_space:
                _cm.register_space_callback(on_space)
            _cm.register_exit_callback(on_capture_exit)
            _capture_mod = _cm
        except ImportError:
            pass

        deadline = None if timeout_seconds is None else (time.time() + timeout_seconds)

        try:
            if not self.receive:
                # Not receiving — nothing to poll; console / space / timeout only.
                while not finished.is_set():
                    if deadline is not None and time.time() >= deadline:
                        finish(NotificationResult(source="timeout"))
                        break
                    finished.wait(timeout=0.1)

            elif self._dispatcher_is_running():
                # Dispatcher is the single owner of the update stream.
                # Register a temporary sink so it routes our updates here.
                def _update_sink(update: dict[str, Any]) -> bool:
                    if finished.is_set():
                        return False

                    callback = update.get("callback_query")
                    if callback:
                        cb_message = callback.get("message") or {}
                        cb_message_id = cb_message.get("message_id")
                        cb_chat_id = str((cb_message.get("chat") or {}).get("id"))
                        cb_data = callback.get("data", "")

                        if cb_message_id == message_id and cb_chat_id == self.chat_id:
                            if cb_data.startswith(token_prefix):
                                try:
                                    self._answer_callback_query(
                                        callback["id"], text="Received"
                                    )
                                except Exception:
                                    pass
                                finish(
                                    NotificationResult(
                                        source="telegram",
                                        action_value=cb_data[len(token_prefix) :],
                                        callback_query_id=callback["id"],
                                        raw_update=update,
                                    )
                                )
                                return True

                    if wait_for_text and not finished.is_set():
                        msg = update.get("message")
                        if msg:
                            msg_chat_id = str((msg.get("chat") or {}).get("id"))
                            if msg_chat_id == self.chat_id:
                                response_message_id[0] = msg.get("message_id")
                                finish(
                                    NotificationResult(
                                        source="telegram",
                                        action_value=msg.get("text", ""),
                                        raw_update=update,
                                    )
                                )
                                return True

                    return False

                self._add_sink(_update_sink)
                try:
                    if timeout_seconds is None:
                        while not finished.is_set():
                            finished.wait(timeout=0.1)
                    else:
                        finished.wait(timeout=timeout_seconds)
                        if not finished.is_set():
                            finish(NotificationResult(source="timeout"))
                except KeyboardInterrupt:
                    do_delete_question()
                    raise
                finally:
                    self._remove_sink(_update_sink)

            else:
                # No dispatcher — poll directly as before.
                try:
                    while not finished.is_set():
                        if deadline is not None and time.time() >= deadline:
                            finish(NotificationResult(source="timeout"))
                            break

                        wait_time = 10
                        if deadline is not None:
                            remaining = deadline - time.time()
                            if remaining <= 0:
                                finish(NotificationResult(source="timeout"))
                                break
                            wait_time = max(1, min(wait_time, int(remaining)))

                        updates = self._get_updates(
                            timeout=wait_time, allowed_updates=allowed_updates
                        )

                        for update in updates:
                            if finished.is_set():
                                break

                            callback = update.get("callback_query")
                            if callback:
                                cb_message = callback.get("message") or {}
                                cb_message_id = cb_message.get("message_id")
                                cb_chat_id = str(
                                    (cb_message.get("chat") or {}).get("id")
                                )
                                cb_data = callback.get("data", "")

                                if (
                                    cb_message_id == message_id
                                    and cb_chat_id == self.chat_id
                                ):
                                    if cb_data.startswith(token_prefix):
                                        try:
                                            self._answer_callback_query(
                                                callback["id"], text="Received"
                                            )
                                        except Exception:
                                            pass
                                        finish(
                                            NotificationResult(
                                                source="telegram",
                                                action_value=cb_data[
                                                    len(token_prefix) :
                                                ],
                                                callback_query_id=callback["id"],
                                                raw_update=update,
                                            )
                                        )
                                        break

                            if wait_for_text and not finished.is_set():
                                msg = update.get("message")
                                if msg:
                                    msg_chat_id = str((msg.get("chat") or {}).get("id"))
                                    if msg_chat_id == self.chat_id:
                                        response_message_id[0] = msg.get("message_id")
                                        finish(
                                            NotificationResult(
                                                source="telegram",
                                                action_value=msg.get("text", ""),
                                                raw_update=update,
                                            )
                                        )
                                        break

                except KeyboardInterrupt:
                    do_delete_question()
                    raise

        finally:
            if _capture_mod is not None:
                try:
                    if catch_space:
                        _capture_mod.unregister_space_callback(on_space)
                    _capture_mod.unregister_exit_callback(on_capture_exit)
                except Exception:
                    pass

        return result_box["result"]

    def send_media_group(
        self,
        images: list,
        captions: list[str] | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Send up to 10 photos as a Telegram media group (album).

        Parameters
        ----------
        images:
            List of file paths (str or Path) to send.
        captions:
            Optional per-image captions (only the first caption is shown by
            Telegram for the whole album, but we attach them for context).
        """
        if not images:
            raise ValueError("images list must not be empty")
        if len(images) > 10:
            raise ValueError("Telegram allows at most 10 items per media group")

        cap_list: list[str | None] = (
            list(captions) if captions else [None] * len(images)
        )

        media_json: list[dict[str, Any]] = []
        files: dict[str, dict[str, Any]] = {}

        for i, img in enumerate(images):
            path = str(img)
            if not os.path.isfile(path):
                raise FileNotFoundError(f"Image not found: {path}")
            key = f"photo{i}"
            item: dict[str, Any] = {"type": "photo", "media": f"attach://{key}"}
            if i < len(cap_list) and cap_list[i]:
                item["caption"] = cap_list[i]
            media_json.append(item)
            files[key] = {
                "filename": os.path.basename(path),
                "content_type": mimetypes.guess_type(path)[0] or "image/png",
                "data": self._read_file_bytes(path),
            }

        fields: dict[str, str] = {
            "chat_id": self.chat_id,
            "media": json.dumps(media_json),
            "disable_notification": json.dumps(disable_notification),
        }

        return self._post_multipart("sendMediaGroup", fields=fields, files=files)

    def delete_message(self, *, message_id: int) -> dict[str, Any]:
        return self._post_json(
            "deleteMessage",
            {"chat_id": self.chat_id, "message_id": message_id},
        )

    # ------------------------------------------------------------------ commands

    def register_command(self, command: str, handler, description: str = "") -> None:
        """Register a handler for a bot command and sync with the Telegram commands menu.

        Parameters
        ----------
        command:
            Command name with or without the leading slash (e.g. ``"screenshot"``
            or ``"/screenshot"``).  Case-insensitive.
        handler:
            Callable with signature ``handler(args: str) -> None`` where *args*
            is everything that follows the command on the same line (empty string
            when nothing follows).
        description:
            Short description shown in the Telegram command menu (max 256 chars).
            Defaults to the command name if omitted.
        """
        with self._commands_lock:
            self._commands[command.lstrip("/").lower()] = (handler, description)
        self._sync_commands()

    def unregister_command(self, command: str) -> None:
        """Remove a registered command handler and sync with the Telegram commands menu."""
        with self._commands_lock:
            self._commands.pop(command.lstrip("/").lower(), None)
        self._sync_commands()

    def _sync_commands(self) -> None:
        """Push the current command list to Telegram via setMyCommands."""
        if not self.receive:
            return
        with self._commands_lock:
            items = list(self._commands.items())
        if not items:
            try:
                self._post_json("deleteMyCommands", {})
            except Exception:
                pass
            return
        payload = [
            {"command": cmd, "description": desc or cmd} for cmd, (_, desc) in items
        ]
        try:
            self._post_json("setMyCommands", {"commands": json.dumps(payload)})
        except Exception as e:
            pass

    # ------------------------------------------------------- command listener

    def start_command_listener(self) -> None:
        """Start the background dispatcher that polls for incoming commands.

        Safe to call multiple times — a second call is a no-op if the
        dispatcher is already running.  The dispatcher also handles updates for
        :meth:`send_and_wait` so both can coexist on the same update stream.
        """
        if not self.receive:
            return
        if self._dispatcher_thread is not None and self._dispatcher_thread.is_alive():
            return
        self._sync_offset(allowed_updates=["message", "callback_query"])
        self._dispatcher_stop.clear()
        self._dispatcher_thread = threading.Thread(
            target=self._dispatcher_loop,
            daemon=True,
            name="TelegramDispatcher",
        )
        self._dispatcher_thread.start()

    def stop_command_listener(self) -> None:
        """Stop the background dispatcher (no-op if not running)."""
        self._dispatcher_stop.set()

    def _dispatcher_is_running(self) -> bool:
        # if self doesn't contain _dispatcher_thread, it is not running
        if not hasattr(self, "_dispatcher_thread"):
            return False
        return (
            self._dispatcher_thread is not None
            and self._dispatcher_thread.is_alive()
            and not self._dispatcher_stop.is_set()
        )

    def _add_sink(self, sink) -> None:
        with self._update_sinks_lock:
            self._update_sinks.append(sink)

    def _remove_sink(self, sink) -> None:
        with self._update_sinks_lock:
            try:
                self._update_sinks.remove(sink)
            except ValueError:
                pass

    def _dispatcher_loop(self) -> None:
        while not self._dispatcher_stop.is_set():
            try:
                updates = self._get_updates(
                    timeout=5,
                    allowed_updates=["message", "callback_query"],
                )
            except Exception:
                time.sleep(1)
                continue
            for update in updates:
                if self._dispatcher_stop.is_set():
                    break
                self._dispatch_update(update)

    def _dispatch_update(self, update: dict[str, Any]) -> None:
        # Sinks first — send_and_wait handlers get first refusal.
        with self._update_sinks_lock:
            sinks = list(self._update_sinks)
        for sink in sinks:
            try:
                if sink(update):
                    return
            except Exception:
                pass

        # Command dispatch — use message entities (type "bot_command") as per
        # https://core.telegram.org/bots/features#commands
        msg = update.get("message") or {}
        text = msg.get("text") or ""
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if chat_id != self.chat_id:
            return
        for entity in msg.get("entities") or []:
            if entity.get("type") != "bot_command":
                continue
            offset = entity["offset"]
            length = entity["length"]
            # Not receivingsets are in UTF-16 code units
            text_utf16 = text.encode("utf-16-le")
            raw_cmd = text_utf16[offset * 2 : (offset + length) * 2].decode("utf-16-le")
            # raw_cmd is e.g. "/screenshot" or "/screenshot@botname"
            cmd = raw_cmd.lstrip("/").split("@")[0].lower()
            # args = everything after the command entity (stripped)
            after = text_utf16[(offset + length) * 2 :].decode("utf-16-le").strip()
            with self._commands_lock:
                entry = self._commands.get(cmd)
            if entry is not None:
                handler, _ = entry
                threading.Thread(target=handler, args=(after,), daemon=True).start()
            break  # only handle the first bot_command entity per message

    # ---------------------------------------------------------------- private

    def _start_timeout_deleter(self, message_id: int, timeout_seconds: float) -> None:
        """Non-blocking: delete *message_id* after *timeout_seconds* elapses
        OR when the app stops — whichever comes first."""

        def _run() -> None:
            self._app_stopping.wait(timeout=timeout_seconds)
            try:
                self.delete_message(message_id=message_id)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _start_close_listener(self, message_id: int) -> None:
        """Non-blocking: delete *message_id* when the user presses the close
        button (callback_data == "close_auto").  Skipped if the app is stopping
        so messages are not deleted on exit."""

        def do_close() -> None:
            if self._app_stopping.is_set():
                return
            try:
                self.delete_message(message_id=message_id)
            except Exception:
                pass

        if self._dispatcher_is_running():
            # Reuse the dispatcher's update stream via a temporary sink.
            def _sink(update: dict[str, Any]) -> bool:
                if self._app_stopping.is_set():
                    self._remove_sink(_sink)
                    return False
                cb = update.get("callback_query")
                if not cb:
                    return False
                cb_msg = cb.get("message") or {}
                if cb_msg.get("message_id") != message_id:
                    return False
                if str((cb_msg.get("chat") or {}).get("id")) != self.chat_id:
                    return False
                if cb.get("data") != "close_auto":
                    return False
                try:
                    self._answer_callback_query(cb["id"])
                except Exception:
                    pass
                self._remove_sink(_sink)
                threading.Thread(target=do_close, daemon=True).start()
                return True

            self._add_sink(_sink)
        else:
            # No dispatcher — poll in a dedicated daemon thread.  This thread
            # owns its own offset slice so it does not interfere with any
            # concurrent send_and_wait call's offset tracking.
            def _poll() -> None:
                while not self._app_stopping.is_set():
                    try:
                        updates = self._get_updates(
                            timeout=10, allowed_updates=["callback_query"]
                        )
                    except Exception:
                        time.sleep(1)
                        continue
                    for update in updates:
                        cb = update.get("callback_query")
                        if not cb:
                            continue
                        cb_msg = cb.get("message") or {}
                        if cb_msg.get("message_id") != message_id:
                            continue
                        if str((cb_msg.get("chat") or {}).get("id")) != self.chat_id:
                            continue
                        if cb.get("data") != "close_auto":
                            continue
                        try:
                            self._answer_callback_query(cb["id"])
                        except Exception:
                            pass
                        do_close()
                        return

            threading.Thread(target=_poll, daemon=True).start()

    def _encode_frame_for_telegram(
        self,
        *,
        frame: np.ndarray,
        image_format: str,
        jpeg_quality: int,
        png_compression: int,
        filename: str | None,
    ) -> tuple[bytes, str, str]:
        if frame is None:
            raise ValueError("frame cannot be None")

        if not isinstance(frame, np.ndarray):
            raise TypeError("frame must be a numpy.ndarray")

        if frame.size == 0:
            raise ValueError("frame is empty")

        fmt = image_format.lower()
        if fmt not in (".jpg", ".jpeg", ".png"):
            raise ValueError("image_format must be one of: .jpg, .jpeg, .png")

        params: list[int] = []
        if fmt in (".jpg", ".jpeg"):
            params = [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]
            content_type = "image/jpeg"
            used_filename = filename or "frame.jpg"
        else:
            params = [int(cv2.IMWRITE_PNG_COMPRESSION), int(png_compression)]
            content_type = "image/png"
            used_filename = filename or "frame.png"

        ok, encoded = cv2.imencode(fmt, frame, params)
        if not ok:
            raise RuntimeError("Failed to encode OpenCV frame")

        return encoded.tobytes(), used_filename, content_type

    def _build_reply_markup(
        self,
        actions: list[TelegramAction] | None,
    ) -> dict[str, Any] | None:
        if not actions:
            return None
        return {
            "inline_keyboard": [
                [{"text": action.text, "callback_data": action.value}]
                for action in actions
            ]
        }

    def _sync_offset(self, allowed_updates: list[str] | None = None) -> None:
        while True:
            updates = self._get_updates(timeout=0, allowed_updates=allowed_updates)
            if not updates:
                return

    def _get_updates(
        self, *, timeout: int = 10, allowed_updates: list[str] | None = None
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(allowed_updates or ["callback_query"]),
        }

        with self._offset_lock:
            if self._offset is not None:
                payload["offset"] = self._offset

        response = self._post_json("getUpdates", payload)
        updates = response["result"]

        if updates:
            last_update_id = updates[-1]["update_id"]
            with self._offset_lock:
                self._offset = last_update_id + 1

        return updates

    def _answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._post_json("answerCallbackQuery", payload)

    def _post_json(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        encoded = parse.urlencode(data).encode("utf-8")
        req = request.Request(
            url=f"{self.api_base}/{method}",
            data=encoded,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return self._read_json(req)

    def _post_multipart(
        self,
        method: str,
        *,
        fields: dict[str, str],
        files: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        content_type, body = self._encode_multipart_formdata(fields, files)
        req = request.Request(
            url=f"{self.api_base}/{method}",
            data=body,
            method="POST",
            headers={"Content-Type": content_type},
        )
        return self._read_json(req)

    def _read_json(self, req: request.Request) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=self.request_timeout) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as exc:
            raise RuntimeError(f"Telegram API request failed: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid Telegram API response: {raw}") from exc

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")

        return data

    def _encode_multipart_formdata(
        self,
        fields: dict[str, str],
        files: dict[str, dict[str, Any]],
    ) -> tuple[str, bytes]:
        boundary = f"----TelegramNotifierBoundary{uuid.uuid4().hex}"
        body = bytearray()

        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            )
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")

        for field_name, file_info in files.items():
            filename = file_info["filename"]
            content_type = file_info.get("content_type", "application/octet-stream")
            data = file_info["data"]

            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
            )
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.extend(data)
            body.extend(b"\r\n")

        body.extend(f"--{boundary}--\r\n".encode())
        return f"multipart/form-data; boundary={boundary}", bytes(body)

    @staticmethod
    def _read_file_bytes(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()
