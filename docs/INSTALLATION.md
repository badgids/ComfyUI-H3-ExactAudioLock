# Installation and Python dependencies

This guide covers installation of ComfyUI-H3-ExactAudioLock and its Python dependency in the same environment that runs ComfyUI.

## Requirements

ComfyUI-H3-ExactAudioLock expects:

- a current ComfyUI installation with `comfy_api.latest` and native `Autogrow`;
- MiniMax H3 support in ComfyUI;
- Python 3.11+;
- PyTorch and TorchAudio from the active ComfyUI environment; and
- PyAV `14.2.0` or newer.

The repository includes:

```text
requirements.txt
```

with:

```text
av>=14.2.0
```

PyTorch and TorchAudio are intentionally not listed in this repository's requirements file. ComfyUI installs those packages for the machine's selected CPU/GPU backend. A custom-node requirements file should not replace a working CUDA, ROCm, XPU, or other backend-specific Torch installation.

Current ComfyUI also includes PyAV in its own dependency set. Declaring PyAV here makes the custom node's direct dependency explicit and gives manual/custom-node installers a standard `pip install -r requirements.txt` path.

## Install with Git

Clone the repository into ComfyUI's `custom_nodes` directory:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone https://github.com/badgids/ComfyUI-H3-ExactAudioLock.git
cd ComfyUI-H3-ExactAudioLock
```

Then install this repository's requirements with the Python interpreter that actually runs ComfyUI.

## Linux, WSL2, and macOS with `ComfyUI/.venv`

When the repository is located at:

```text
ComfyUI/custom_nodes/ComfyUI-H3-ExactAudioLock/
```

and ComfyUI's virtual environment is:

```text
ComfyUI/.venv/
```

run:

```bash
../../.venv/bin/python -m pip install -r requirements.txt
```

You can also activate the environment first:

```bash
source ../../.venv/bin/activate
python -m pip install -r requirements.txt
```

## Windows virtual environment

From the custom-node repository directory:

```powershell
..\..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Or activate the environment first:

```powershell
..\..\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## ComfyUI Windows portable

For the standard layout:

```text
ComfyUI_windows_portable/
├── python_embeded/
└── ComfyUI/
    └── custom_nodes/
        └── ComfyUI-H3-ExactAudioLock/
```

run from the custom-node repository directory:

```powershell
..\..\..\python_embeded\python.exe -m pip install -r requirements.txt
```

Do not install the dependency into an unrelated system Python installation if ComfyUI is using its portable interpreter.

## ZIP installation

1. Download and extract the repository.
2. Place `ComfyUI-H3-ExactAudioLock` under `ComfyUI/custom_nodes/`.
3. Open a terminal in the extracted repository directory.
4. Run `python -m pip install -r requirements.txt` with the same interpreter/environment used by ComfyUI.
5. Restart ComfyUI.

## Dependency-aware custom-node installers

A custom-node installer that recognizes `requirements.txt` can use this file directly.

The manual equivalent is always:

```bash
python -m pip install -r requirements.txt
```

where `python` is the interpreter that runs ComfyUI.

## Verify the environment

Check which interpreter is being used:

```bash
python -c "import sys; print(sys.executable)"
```

Then verify the packages used by this node pack:

```bash
python -c "import torch, torchaudio, av; print('torch', torch.__version__); print('torchaudio', torchaudio.__version__); print('av', av.__version__)"
```

If that command succeeds with the same interpreter that starts ComfyUI, the Python dependency layer is ready.

## Updating an existing installation

After pulling repository updates:

```bash
git pull
python -m pip install -r requirements.txt
```

Again, run the second command with the ComfyUI interpreter or activated ComfyUI virtual environment.

Restart ComfyUI after an update that changes Python code, frontend JavaScript, or dependencies.

## Troubleshooting

### `ModuleNotFoundError: No module named 'av'`

The custom-node requirements were not installed in the interpreter currently loading the node.

Run:

```bash
python -m pip install -r requirements.txt
```

with the ComfyUI interpreter.

### `ModuleNotFoundError: No module named 'torchaudio'`

First confirm that the command is using the same Python environment as ComfyUI:

```bash
python -c "import sys; print(sys.executable)"
```

Do not immediately install an arbitrary TorchAudio wheel. Torch and TorchAudio are normally installed as part of ComfyUI's backend-specific environment and need to match each other and the selected hardware backend.

### Requirements were installed but ComfyUI still reports an import error

Compare the interpreter used by the successful package check with the interpreter used to launch ComfyUI. If they differ, install the requirements with the ComfyUI interpreter instead.

### Managed audio-file review fails to decode a file

The Audio Review / Accept Gate uses PyAV for WAV, MP3, FLAC, OGG/OGA, and Opus managed-file candidates. Confirm the file is valid, is located in ComfyUI's managed input directory, and that PyAV imports successfully in the active ComfyUI environment.

## Related documentation

- [README](../README.md)
- [Audio Review / Accept Gate](AUDIO_REVIEW_GATE.md)
- [Scene-aware dialogue](SCENE_DIALOGUE.md)
- [Dialogue-only / partial lock](DIALOGUE_PARTIAL_LOCK.md)
- [Development guide](../DEVELOPMENT.md)
