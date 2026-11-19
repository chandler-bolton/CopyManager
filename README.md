# CopyManager

A clipboard history manager for Windows, inspired by Win+V. It keeps the last *n* items you copied, lets you pin items so they stay separate from that history, and lets you paste items with keyboard shortcuts.

- **Backend (C++, `src/monitor.cpp`)**: watches the clipboard and writes new copies to `src/history.json`. It also handles the 0–9 paste shortcuts.
- **Frontend (Python/Tkinter, `app.pyw`)**: shows history and pinned items, and edits settings and preferences.

The two sides only talk through the JSON files below.

## Running

You can either manually build the clip board monitor first using `build_monitor.bat`. Or you can start the GUI which builds and initializes it automatically too.

The GUI starts `bin\monitor.exe` automatically, and the monitor stops when the GUI closes. If the monitor is missing or keeps crashing, the status bar shows a warning instead of "Capturing".

### Manually Building the Clipboard Monitor .exe

Build the clipboard monitor once, and again whenever `src/monitor.cpp` changes. Close CopyManager first:

```
build_monitor.bat   # g++ (MinGW-w64 / TDM-GCC) -> bin\monitor.exe
```

`build_monitor.bat` works with CopyManager open: it moves the running `monitor.exe` aside, and **Save & refresh** then restarts the monitor on the new build.

Then start the app.

### Starting the App/GUI
*Note: The app will automatically build and initialize the Clipboard Monitor .exe file. Manually doing this process is opitonal.*

To start the app, either open up your IDE/terminal of choice in the same directory as `app.pyw` and type one of the following commands:
```
pythonw app.pyw     # no console window
python  app.pyw     # with a console, for debugging
```

*Note: These commands are for Windows CMD - modifiy as needed for other terminals*

**OR**

Double-click on `app.pyw` with your mouse/cursor.

## Paste shortcuts (`src/monitor.cpp`)

This app offers shortcuts for up to the last 10 items you copied. The shortcuts can be modified in the **Preferences** menu.
To use the shortcuts, you would do the following:
- `<modifiers>+0`-`9` on your keyboard itself (not numpad)

The app presents a couple default variants to choose from, but your own custom shortcut can be used too.
The app will also notify you if a shortcut combination is a registered Windows default combination and asks for confirmation in these cases.
- **Important:** The app **ONLY** knows the registered shortcuts within the app itself and Windows native shortcuts. Shortcuts from other apps are not known.

You can further customize pasting with shortcuts in the **Preferences** menu. Current options include:
- Paste text only
- Paste format only
- Add space afer paste
- Add new line after paste

## Running as administrator

Windows blocks a normal app from sending keystrokes to an app that runs as administrator, so shortcut pastes into admin apps need CopyManager to be elevated too. The monitor is started by the GUI, so it inherits the same rights.

- **Settings → Always run as administrator:** every launch, CopyManager relaunches itself through the UAC prompt. If the prompt is cancelled, it carries on without admin rights and says so.
- **Sidebar → Restart as administrator:** elevates this session only. The setting isn't changed, so the next normal launch is a regular one. The button is hidden while CopyManager is already elevated, and the status bar shows "Administrator".
- **How the handover works:** the new copy is started with `--after-pid <old pid>` and waits for the old copy (and its monitor) to exit completely before it starts. `--no-elevate` stops it from relaunching itself again.

## Status Bar (Bottom)

The status bar on the bottom of the application itself shows several key details:
1. If capturing is active
2. The current stored entries / max entries allowed
3. Amount of pinned entries
4. Current Shortcut Schema
5. Last synced date and time (aka, when you last copied or pasted something or used the app)

In addition to that, the color of the bar itself also gives some information too.
- **Blue** | Operating normally with no errors
- **Orange** | An error occured somewhere **OR** the capturing is not enabled/working correctly
- **Red** | App is running with adminstrative privileges

## Shutting down

Closing the window, Windows signing out, or the window being destroyed some other way all run `App.shutdown()`. It cancels the repeating checks, stops the pop-out shortcut thread, stops the monitor (waiting until it has exited), and detaches the log listeners. An `atexit` hook calls it again as a safety net. If the app is killed outright (e.g. Task Manager), the monitor notices its parent has exited and stops on its own.


## Settings vs. preferences

- **Settings** control the app itself: run on startup, window behaviour, theme, and font.
- **Preferences** control how clipboard capture and shortcut pasting work: history size, how duplicates are handled, paste format, and so on. The C++ monitor reads these.

**IMPORTANT:**
- All changes made **MUST** be saved and refreshed to take affect!

## Logging

You can see what info is being logged through the `Output` option under `Tools` in the GUI.

This shows the activity of CopyManager.

The monitor can append plain-text lines to `logs/monitor.log`. The Output tab follows that file live, and lines containing `ERROR` or `WARN` are highlighted.


## AI Assistance Disclosure

AI was used to help build this app. 

AI was used to help speed up the production process, along with more automated tests. The app idea itself is also not the most complex; just many steps, which AI tends to be pretty good.
Additionally, despite knowing C++, I am not the most apt in it, and opted to use AI in helping with that to ensure it actually works.

### Where AI was used

AI was used in the following areas:
1. Automated testing
2. Assisted-Code building
3. Debug assistance 
4. ReadMe document (Initial)

**All** AI generated code was manually reviewed and altered as needed.

### Where AI was not used (where I handled it):

AI was not used in the following areas:
1. Architecturing
2. Design
3. Foundational elements (i.e., initial scripts, folder layouts, what packages were and weren't going to be used, etc.)
4. Schema
5. Manual testing
6. Actual debugging 
7. Effeciency patches

## Q&A:

1. Why was Python and C++ used?
- Personal preference. Python is the language I am most familiar with, and I have made many GUI applications using it. It's also the language I tend to favor the most. C++ was used for the backend due to it's native Windows API connections and capabilites and overall speed. Other languages could have also done this, and you could easily do this with one language too. I just like using Python and C++ when I can.
2. Does this track/record user-data?
- No. Everything that is tracked is saved locally to your machine. Nothing is sent outside of it. It's not sent to a server or anything like that. I also don't want to know what other people are copying.
3. Why use AI?
- Briefly: to make this process faster. This would have been a surprisingly tedious process with a lot of different steps and demanded time that I don't simply have (or even want to give). I had an issue that is nuanced but wanted to fix, and wanted to share it too. I'm also not the best at C++ and wanted to use it. So AI helped make sure that it was possible.

## Known Bugs/Issues
1. When entering a new Pinned Item manually, the cursor for the label sometimes gets wonky
2. The actual title of the app is spelt in two different ways throughout the app: `Copy Manager` and `CopyManager`
3. Some animations are a bit jittery - sometimes consistently so (such as opening and closing the side-panel), and sometimes more random (such as collapsing the options)
4. When the side-panel is minimized and all accordions are minimized, there is no visual way to tell what each section is for withouth either opening it, or hovering over it with you mouse

