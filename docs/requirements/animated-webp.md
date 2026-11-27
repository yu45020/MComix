# Animated WebP playback

Code: the "Animated WebP" row of the AGENTS.md Roadmap.

## Playback

- An animated WebP page plays, from a file or from an archive, with the frame durations and the
  loop count the file encodes. There is no fixed frame rate.
- Every frame is shown, in order. A frame is shown for at least its encoded duration; when
  decoding or resizing is late, playback takes longer and never skips frames or catches up in a
  burst.
- Playback works with every zoom mode, rotation, flips, image enhancement and two visible pages.
  Resizing the window or switching fullscreen keeps the playback position.
- With `animation mode` set to Never, the first frame is shown as a still page.
- A decoding error is logged and the page falls back to its first frame.
- Changing page, replacing the page list, closing the book and quitting stop playback and
  release its memory.

## Other consumers

- Thumbnails, the smart background and Edit > Copy use the first frame.
- Animated pages are never neurally upscaled and never use the static page render cache.

## Size

- `enlarge animated webp` off shows a WebP page no larger than its original size; it is still
  shrunk to fit. Static pages are unaffected.
- `animated webp max size` limits the longest displayed edge, keeping the aspect ratio; 0 means
  no limit. It applies to layout, the first frame and playback, and not to static images.

## Memory

- The frames kept for playback and the resized output of all visible animations stay within
  `animation cache limit`. The whole animation is never loaded ahead.
- An animation that can't fit shows its first frame, with a notice over the top of the page that
  offers to open the settings. The notice needs no answer, doesn't change the layout, and goes
  away when the page changes, animation is turned off or playback fits again.
- Changing the limit applies at once: raising it keeps running playback at its position and
  restarts playback the limit had stopped.
