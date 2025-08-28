import streamlit as st
import json
import os
from datetime import datetime
import time
from typing import Dict, List, Optional
import uuid
from pathlib import Path

# Google Gen AI imports
try:
    from google import genai
    from google.genai import types
except ImportError:
    st.error("Install the Google Gen AI SDK: pip install google-genai")
    st.stop()

# Configure page
st.set_page_config(page_title="Veo 3 Video Generator", layout="wide")

# Initialize session state
if "sessions" not in st.session_state:
    st.session_state.sessions = {}
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None
if "api_key_configured" not in st.session_state:
    st.session_state.api_key_configured = False
if "client" not in st.session_state:
    st.session_state.client = None
if "generated_videos_dir" not in st.session_state:
    st.session_state.generated_videos_dir = Path("generated_videos")
    st.session_state.generated_videos_dir.mkdir(exist_ok=True)

# Constants
ASPECT_RATIOS = {
    "16:9 (Widescreen)": "16:9",
    "9:16 (Portrait)": "9:16",
    "1:1 (Square)": "1:1",
    "4:3 (Standard)": "4:3",
    "3:2 (Photo)": "3:2",
    "21:9 (Cinema)": "21:9",
    "4:5 (Portrait)": "4:5",
    "2:3 (Portrait)": "2:3",
}

MODEL_VERSIONS = {
    "Veo 3 Fast": "veo-3.0-fast-generate-preview",
    "Veo 3 Quality": "veo-3.0-generate-preview",
}

# Session management functions


def create_new_session() -> str:
    """Create a new session and return its ID"""
    session_id = str(uuid.uuid4())[:8]
    st.session_state.sessions[session_id] = {
        "id": session_id,
        "created_at": datetime.now().isoformat(),
        "name": f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "generations": [],
    }
    return session_id


def delete_session(session_id: str):
    """Delete a session and its associated videos"""
    if session_id in st.session_state.sessions:
        # Delete video files associated with this session
        session = st.session_state.sessions[session_id]
        for generation in session.get("generations", []):
            for video in generation.get("videos", []):
                if "local_path" in video and os.path.exists(video["local_path"]):
                    try:
                        os.remove(video["local_path"])
                    except Exception:
                        pass

        del st.session_state.sessions[session_id]
        if st.session_state.current_session_id == session_id:
            st.session_state.current_session_id = None


def save_sessions_to_file():
    """Save sessions to a JSON file for persistence"""
    try:
        sessions_file = st.session_state.generated_videos_dir / "sessions.json"
        # Create a copy without video data for JSON serialization
        sessions_copy = {}
        for sid, session in st.session_state.sessions.items():
            sessions_copy[sid] = {
                "id": session["id"],
                "created_at": session["created_at"],
                "name": session["name"],
                "generations": [],
            }
            for gen in session.get("generations", []):
                gen_copy = {
                    "timestamp": gen["timestamp"],
                    "prompt": gen["prompt"],
                    "settings": gen["settings"],
                    "videos": [],
                }
                for video in gen.get("videos", []):
                    video_copy = {
                        "id": video["id"],
                        "prompt": video["prompt"],
                        "aspect_ratio": video["aspect_ratio"],
                        "model_version": video["model_version"],
                        "created_at": video["created_at"],
                        "local_path": video.get("local_path", ""),
                        "status": video["status"],
                    }
                    gen_copy["videos"].append(video_copy)
                sessions_copy[sid]["generations"].append(gen_copy)

        with open(sessions_file, "w") as f:
            json.dump(sessions_copy, f, indent=2)
    except Exception as e:
        st.error(f"Error saving sessions: {e}")


def load_sessions_from_file():
    """Load sessions from a JSON file"""
    try:
        sessions_file = st.session_state.generated_videos_dir / "sessions.json"
        if sessions_file.exists():
            with open(sessions_file, "r") as f:
                st.session_state.sessions = json.load(f)
        else:
            legacy_file = Path("veo3_sessions.json")
            if legacy_file.exists():
                with open(legacy_file, "r") as f:
                    st.session_state.sessions = json.load(f)
                with open(sessions_file, "w") as f:
                    json.dump(st.session_state.sessions, f, indent=2)
    except Exception as e:
        st.warning(f"Could not load previous sessions: {e}")


def generate_videos_with_veo3(
    prompt: str,
    aspect_ratio: str,
    model_version: str,
    num_variations: int,
    image_gcs_uri: Optional[str] = None,
    image_mime_type: str = "image/png",
) -> List[Dict]:
    """
    Generate videos using Google's Veo 3 API
    """
    videos = []

    if not st.session_state.client:
        st.error("Client not initialized. Please configure your API key.")
        return videos

    try:
        for i in range(num_variations):
            video_id = str(uuid.uuid4())[:8]

            # Update progress
            progress_text = f"Generating video {i+1} of {num_variations}..."
            progress_bar = st.progress(
                (i) / num_variations,
                text=progress_text,
            )

            # Create the generation operation with aspect ratio configuration
            config = types.GenerateVideosConfig(
                aspect_ratio=aspect_ratio,
                number_of_videos=1,
            )

            request_kwargs = {
                "model": model_version,
                "prompt": prompt,
                "config": config,
            }
            if image_gcs_uri:
                request_kwargs["image"] = types.Image(
                    gcs_uri=image_gcs_uri,
                    mime_type=image_mime_type,
                )
            operation = st.session_state.client.models.generate_videos(**request_kwargs)

            # Poll the operation status until the video is ready
            poll_count = 0
            max_polls = 60  # Maximum 10 minutes (60 * 10 seconds)

            while not operation.done and poll_count < max_polls:
                time.sleep(10)
                operation = st.session_state.client.operations.get(operation)
                poll_count += 1

                # Update progress text
                elapsed_time = poll_count * 10
                progress_bar.progress(
                    (i + 0.5) / num_variations,
                    text=(
                        f"Generating video {i+1} of {num_variations}... "
                        f"({elapsed_time}s elapsed)"
                    ),
                )

            if operation.done:
                # Download the generated video
                generated_video = operation.response.generated_videos[0]

                # Save video to local file
                video_filename = (
                    f"veo3_{video_id}_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                )
                video_path = st.session_state.generated_videos_dir / video_filename

                # Download and save the video
                st.session_state.client.files.download(file=generated_video.video)
                generated_video.video.save(str(video_path))

                # Create video data entry
                video_data = {
                    "id": video_id,
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "model_version": model_version,
                    "created_at": datetime.now().isoformat(),
                    "local_path": str(video_path),
                    "status": "completed",
                }
                videos.append(video_data)

                # Update progress
                progress_bar.progress(
                    (i + 1) / num_variations,
                    text=(f"Completed video {i+1} " f"of {num_variations}"),
                )
            else:
                st.warning(
                    f"Video {i+1} generation timed out after "
                    f"{max_polls * 10} seconds"
                )
                video_data = {
                    "id": video_id,
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "model_version": model_version,
                    "created_at": datetime.now().isoformat(),
                    "local_path": None,
                    "status": "timeout",
                }
                videos.append(video_data)

            # Clear progress bar after each video
            if i == num_variations - 1:
                progress_bar.empty()

    except Exception as e:
        st.error(f"Error generating videos: {str(e)}")
        st.info(
            "Make sure you have access to the Veo 3 API. "
            "It may be in limited preview."
        )

    return videos


def display_video_card(video: Dict, col):
    """Display a video card with preview and download"""
    with col:
        with st.container():
            st.markdown(f"**Video ID:** {video['id']}")
            st.markdown(f"**Created:** {video['created_at'][:19]}")
            st.markdown(f"**Status:** {video['status']}")

            # Video preview
            if video.get("local_path") and os.path.exists(video["local_path"]):
                try:
                    # Display the actual video
                    with open(video["local_path"], "rb") as video_file:
                        video_bytes = video_file.read()
                    st.video(video_bytes)

                    # Download button
                    st.download_button(
                        label="Download Video",
                        data=video_bytes,
                        file_name=f"veo3_{video['id']}.mp4",
                        mime="video/mp4",
                        key=f"download_{video['id']}_{video['created_at']}",
                    )
                except Exception as e:
                    st.error(f"Error loading video: {e}")
            elif video["status"] == "timeout":
                st.warning("Video generation timed out")
            else:
                st.info("Video not available")


# Main app layout


def main():
    # Load sessions on startup
    if not st.session_state.sessions:
        load_sessions_from_file()

    st.title("Veo 3 Video Generator")
    st.markdown("Generate videos with audio using Google's Veo 3 models")

    # Sidebar for configuration and session management
    with st.sidebar:
        st.header("Configuration")

        # API Key configuration
        api_key = st.text_input(
            "Google AI API Key",
            type="password",
            help=(
                "Enter your Google AI API key to use Veo 3. "
                "Get one at https://aistudio.google.com/app/apikey"
            ),
        )
        if api_key:
            try:
                st.session_state.client = genai.Client(api_key=api_key)
                st.session_state.api_key_configured = True
                st.success("API Key configured")
            except Exception as e:
                st.error(f"Error configuring API: {e}")
                st.session_state.api_key_configured = False

        st.divider()

        # Session Management
        st.header("Sessions")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("➕ New Session", use_container_width=True):
                new_session_id = create_new_session()
                st.session_state.current_session_id = new_session_id
                save_sessions_to_file()
                st.rerun()

        with col2:
            if st.button("Save Sessions", use_container_width=True):
                save_sessions_to_file()
                st.success("Sessions saved!")

        # List existing sessions
        if st.session_state.sessions:
            st.subheader("Existing Sessions")
            for session_id, session in st.session_state.sessions.items():
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    if st.button(
                        f"Open {session['name'][:20]}",
                        key=f"select_{session_id}",
                        use_container_width=True,
                    ):
                        st.session_state.current_session_id = session_id
                        st.rerun()
                with col2:
                    if st.button("Rename", key=f"rename_{session_id}"):
                        # You could add rename functionality here
                        pass
                with col3:
                    if st.button("🗑️", key=f"delete_{session_id}"):
                        delete_session(session_id)
                        save_sessions_to_file()
                        st.rerun()

        # Info section
        st.divider()
        st.info(
            "**Note:** Veo 3 generates 8-second videos with native audio. "
            "Each video generation typically takes 1-3 minutes."
        )

    # Main content area
    if not st.session_state.current_session_id:
        st.info("Please create or select a session to get started")
        return

    current_session = st.session_state.sessions[st.session_state.current_session_id]
    st.subheader(f"Current Session: {current_session['name']}")

    # Video generation form
    with st.form("video_generation_form"):
        st.header("Generate Videos")

        col1, col2 = st.columns(2)

        with col1:
            prompt = st.text_area(
                "Prompt",
                placeholder=(
                    "Describe the video you want to generate. "
                    "Include details about visuals, camera movement, "
                    "and any dialogue or sound effects..."
                ),
                height=150,
                help=(
                    "Be descriptive! Include visual details, camera angles, "
                    "movements, and any audio elements you want."
                ),
            )

            aspect_ratio = st.selectbox(
                "Aspect Ratio",
                options=list(ASPECT_RATIOS.keys()),
                index=0,
                help="Choose the aspect ratio for your video",
            )

        with col2:
            model_version = st.selectbox(
                "Model Version",
                options=list(MODEL_VERSIONS.keys()),
                index=0,
                help=(
                    "Preview model offers higher quality, "
                    "Fast model generates quicker"
                ),
            )

            num_variations = st.select_slider(
                "Number of Variations",
                options=[1, 2, 4],
                value=1,
                help="Generate multiple variations of the same prompt",
            )

            image_gcs_uri = st.text_input(
                "Image GCS URI (optional)",
                placeholder="gs://bucket/path/to/image.png",
                help=(
                    "Provide a Google Cloud Storage URI to guide generation. "
                    "Leave blank to generate from prompt only."
                ),
            )

            image_mime_type = st.selectbox(
                "Image MIME type",
                options=["image/png", "image/jpeg"],
                index=0,
                help="MIME type for the provided image",
            )

            st.markdown("**Video Duration:** 8 seconds")
            st.markdown("**Audio:** Native audio generation included")

        generate_button = st.form_submit_button(
            "Generate Videos", type="primary", use_container_width=True
        )

    # Handle video generation
    if generate_button:
        if not prompt:
            st.error("Please enter a prompt")
        elif not st.session_state.api_key_configured:
            st.error("Please configure your API key in the sidebar")
        else:
            with st.spinner(
                f"Generating {num_variations} video(s)... "
                "This may take a few minutes."
            ):
                videos = generate_videos_with_veo3(
                    prompt=prompt,
                    aspect_ratio=ASPECT_RATIOS[aspect_ratio],
                    model_version=MODEL_VERSIONS[model_version],
                    num_variations=num_variations,
                    image_gcs_uri=(image_gcs_uri or None),
                    image_mime_type=image_mime_type,
                )

                if videos:
                    # Add to current session
                    generation = {
                        "timestamp": datetime.now().isoformat(),
                        "prompt": prompt,
                        "settings": {
                            "aspect_ratio": aspect_ratio,
                            "model_version": model_version,
                            "num_variations": num_variations,
                            "image_gcs_uri": image_gcs_uri,
                        },
                        "videos": videos,
                    }
                    current_session["generations"].append(generation)
                    save_sessions_to_file()
                    st.success(f"Generated {len(videos)} video(s)!")
                    st.rerun()

    # Display generation history
    if current_session["generations"]:
        st.header("Generation History")

        for idx, generation in enumerate(reversed(current_session["generations"])):
            with st.expander(
                (
                    f"Generation {len(current_session['generations']) - idx}: "
                    f"{generation['prompt'][:50]}... "
                    f"({generation['timestamp'][:19]})"
                ),
                expanded=(idx == 0),
            ):
                st.markdown(f"**Prompt:** {generation['prompt']}")
                st.markdown(f"**Settings:** {generation['settings']}")

                # Display videos in a grid
                cols = st.columns(min(len(generation["videos"]), 4))
                for i, video in enumerate(generation["videos"]):
                    display_video_card(video, cols[i % len(cols)])
    else:
        st.info(
            "No videos generated yet. " "Use the form above to create your first video!"
        )

    # Footer
    st.divider()
    st.markdown(
        "**Tips for better results:**\n"
        "- Be specific about visual details and camera movements\n"
        "- Include dialogue in quotes for speech generation\n"
        "- Describe sound effects and ambient audio\n"
        "- Veo 3 excels at realistic physics and cinematic styles"
    )


if __name__ == "__main__":
    main()
