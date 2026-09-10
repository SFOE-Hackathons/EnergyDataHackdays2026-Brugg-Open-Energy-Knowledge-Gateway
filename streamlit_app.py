import time
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import agent_app


# ============================================================
# GOAL OF THIS FILE
# ============================================================
# Build a simple demo UI on top of the already-working
# agent_app.py backend.
#
# INPUT
# ------------------------------------------------------------
# User question entered in the browser.
#
# MAIN PROCESS
# ------------------------------------------------------------
# 1. Connect to Cognito / MCP gateway.
# 2. Discover available MCP tools.
# 3. Ask Nemotron to select the best tool.
# 4. Execute the selected MCP tool.
# 5. Build grounded evidence context.
# 6. Ask Nemotron to generate the final answer.
# 7. Clean citations and sources.
#
# OUTPUT
# ------------------------------------------------------------
# - Final grounded answer
# - Official SFOE source links
# - Selected tool
# - Routing reason
# - Tool arguments
# - Agent latency
# ============================================================


# ============================================================
# 1. PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SFOE Open Energy Knowledge Agent",
    page_icon="⚡",
    layout="wide",
)


# ============================================================
# 2. SMALL CUSTOM CSS
# ============================================================
# GOAL
# ------------------------------------------------------------
# Keep the page clean and professional without making the UI
# overly complicated.
# ============================================================

st.markdown(
    """
    <style>
        .block-container {
            max-width: 1100px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }

        .main-title {
            font-size: 2.2rem;
            font-weight: 700;
            margin-bottom: 0.15rem;
        }

        .subtitle {
            color: #666;
            margin-bottom: 1.5rem;
        }

        .answer-card {
            padding: 1.1rem 1.2rem;
            border: 1px solid rgba(120, 120, 120, 0.25);
            border-radius: 12px;
            margin-top: 0.8rem;
            margin-bottom: 1.2rem;
        }

        .source-card {
            padding: 0.8rem 1rem;
            border: 1px solid rgba(120, 120, 120, 0.20);
            border-radius: 10px;
            margin-bottom: 0.7rem;
        }

        .tool-badge {
            display: inline-block;
            padding: 0.25rem 0.55rem;
            border-radius: 8px;
            background: rgba(120, 120, 120, 0.12);
            font-family: monospace;
            font-size: 0.9rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 3. EXAMPLE QUESTIONS
# ============================================================
# GOAL
# ------------------------------------------------------------
# Make the demo easy to use during Hackdays.
#
# These examples intentionally exercise all three MCP tools.
# ============================================================

EXAMPLE_QUESTIONS = {
    "General knowledge":
        "What role does hydropower play in Switzerland?",

    "Timeline":
        "How has photovoltaic production developed from 2020 to 2024?",

    "Chart values":
        "What values are shown in the SFOE chart for renewable electricity production?",
}


# ============================================================
# 4. SHORT TOOL LABEL
# ============================================================

def friendly_tool_name(
    tool_name: str,
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Convert the long MCP tool name into a short label for the UI.
    """

    mapping = {
        agent_app.SEARCH_TOOL_HINT:
            "Knowledge Search",

        agent_app.TIMELINE_TOOL_HINT:
            "Metric Timeline",

        agent_app.CHART_TOOL_HINT:
            "Chart / Table Data",
    }

    return mapping.get(
        tool_name,
        tool_name,
    )


# ============================================================
# 5. INITIALIZE CONNECTION
# ============================================================
# GOAL
# ------------------------------------------------------------
# Authenticate and discover the MCP tools once.
#
# OUTPUT
# ------------------------------------------------------------
# access_token
# tools
#
# NOTE
# ------------------------------------------------------------
# Streamlit reruns the script whenever the user interacts with
# the page. st.cache_resource prevents repeated authentication
# and tools/list calls during a normal demo session.
# ============================================================

@st.cache_resource
def initialize_agent() -> Tuple[
    Optional[str],
    List[Dict[str, Any]],
]:
    """
    GOAL
    ----------------------------------------------------------
    Prepare the SFOE agent backend once per Streamlit session.
    """

    agent_app.validate_configuration()

    if agent_app.cognito_is_configured():
        access_token = (
            agent_app.fetch_access_token()
        )
    else:
        access_token = None

    tools = (
        agent_app.list_mcp_tools(
            access_token
        )
    )

    return (
        access_token,
        tools,
    )


# ============================================================
# 6. RUN ONE AGENT QUESTION
# ============================================================

def run_agent(
    question: str,
    access_token: Optional[str],
    tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Run the same core workflow as agent_app.py, but return the
    data instead of printing it to the terminal.

    INPUT
    ----------------------------------------------------------
    question
    access_token
    discovered MCP tools

    OUTPUT
    ----------------------------------------------------------
    Dictionary containing:
    - selected tool
    - routing reason
    - arguments
    - final answer
    - cited sources
    - latency
    - whether repair was required
    """

    started = time.perf_counter()

    # --------------------------------------------------------
    # STEP 1: TOOL ROUTING
    # --------------------------------------------------------

    selection = (
        agent_app.choose_tool(
            question=question,
            tools=tools,
        )
    )

    tool_name = selection[
        "tool_name"
    ]

    arguments = selection[
        "arguments"
    ]

    repair_attempted = False

    # --------------------------------------------------------
    # STEP 2: MCP TOOL CALL
    # --------------------------------------------------------

    try:
        tool_payload = (
            agent_app.call_mcp_tool(
                tool_name=
                    tool_name,
                arguments=
                    arguments,
                access_token=
                    access_token,
            )
        )

    except Exception as first_error:
        # ----------------------------------------------------
        # Match agent_app.py:
        # one controlled automatic repair attempt.
        # ----------------------------------------------------

        repair_attempted = True

        repaired = (
            agent_app.repair_tool_arguments(
                question=
                    question,
                selection=
                    selection,
                error_message=
                    str(first_error),
                tools=
                    tools,
            )
        )

        selection = repaired

        tool_name = repaired[
            "tool_name"
        ]

        arguments = repaired[
            "arguments"
        ]

        tool_payload = (
            agent_app.call_mcp_tool(
                tool_name=
                    tool_name,
                arguments=
                    arguments,
                access_token=
                    access_token,
            )
        )

    # --------------------------------------------------------
    # STEP 3: PREPARE EVIDENCE
    # --------------------------------------------------------

    (
        context,
        sources,
    ) = (
        agent_app.prepare_agent_context(
            tool_name=
                tool_name,
            tool_payload=
                tool_payload,
        )
    )

    # --------------------------------------------------------
    # STEP 4: GENERATE GROUNDED ANSWER
    # --------------------------------------------------------

    answer = (
        agent_app.generate_answer(
            question=
                question,
            tool_name=
                tool_name,
            context=
                context,
        )
    )

    # --------------------------------------------------------
    # STEP 5: CLEAN SOURCES / CITATIONS
    # --------------------------------------------------------

    (
        answer,
        sources,
    ) = (
        agent_app.clean_answer_and_sources(
            answer=
                answer,
            sources=
                sources,
        )
    )

    elapsed = (
        time.perf_counter()
        - started
    )

    return {
        "question":
            question,
        "tool_name":
            tool_name,
        "tool_label":
            friendly_tool_name(
                tool_name
            ),
        "routing_reason":
            selection.get(
                "reason",
                "",
            ),
        "arguments":
            arguments,
        "answer":
            answer,
        "sources":
            sources,
        "latency_seconds":
            elapsed,
        "repair_attempted":
            repair_attempted,
    }


# ============================================================
# 7. HEADER
# ============================================================

st.markdown(
    '<div class="main-title">⚡ SFOE Open Energy Knowledge Agent</div>',
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="subtitle">
        Ask questions about Swiss energy using official SFOE sources.
        The AI agent automatically selects the appropriate knowledge,
        timeline, or chart-data tool.
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 8. INITIALIZE BACKEND
# ============================================================

try:
    with st.spinner(
        "Connecting to the SFOE knowledge gateway..."
    ):
        (
            access_token,
            tools,
        ) = initialize_agent()

except Exception as error:
    st.error(
        "Could not initialize the SFOE agent."
    )

    st.exception(
        error
    )

    st.stop()


# ============================================================
# 9. SHOW BACKEND STATUS
# ============================================================

with st.expander(
    "System status",
    expanded=False,
):
    col1, col2 = st.columns(
        2
    )

    with col1:
        st.metric(
            "Production model",
            "NVIDIA Nemotron 3 Super",
        )

    with col2:
        st.metric(
            "Available MCP tools",
            len(
                tools
            ),
        )

    st.caption(
        "Gateway tools discovered dynamically:"
    )

    for tool in tools:
        st.code(
            tool.get(
                "name",
                "",
            ),
            language=None,
        )


# ============================================================
# 10. EXAMPLE QUESTION BUTTONS
# ============================================================

st.markdown(
    "#### Example questions"
)

example_columns = st.columns(
    3
)

for (
    column,
    (
        label,
        example_question,
    ),
) in zip(
    example_columns,
    EXAMPLE_QUESTIONS.items(),
):
    with column:
        if st.button(
            label,
            use_container_width=True,
        ):
            st.session_state[
                "question_input"
            ] = example_question


# ============================================================
# 11. QUESTION INPUT
# ============================================================

if (
    "question_input"
    not in st.session_state
):
    st.session_state[
        "question_input"
    ] = ""


question = st.text_area(
    "Ask a question",
    key="question_input",
    height=100,
    placeholder=(
        "Example: How has photovoltaic production "
        "developed from 2020 to 2024?"
    ),
)

ask_button = st.button(
    "Ask the SFOE Agent",
    type="primary",
    use_container_width=True,
)


# ============================================================
# 12. EXECUTE QUESTION
# ============================================================

if ask_button:

    if not question.strip():
        st.warning(
            "Please enter a question."
        )

    else:
        try:
            with st.spinner(
                "The agent is selecting a tool, retrieving SFOE evidence, "
                "and generating a grounded answer..."
            ):
                result = run_agent(
                    question=
                        question.strip(),
                    access_token=
                        access_token,
                    tools=
                        tools,
                )

            st.session_state[
                "last_result"
            ] = result

        except Exception as error:
            st.error(
                "The agent could not complete the request."
            )

            st.exception(
                error
            )


# ============================================================
# 13. DISPLAY LAST RESULT
# ============================================================

result = st.session_state.get(
    "last_result"
)

if result:

    st.divider()

    # --------------------------------------------------------
    # Selected tool + latency
    # --------------------------------------------------------

    info_col1, info_col2 = (
        st.columns(
            [
                3,
                1,
            ]
        )
    )

    with info_col1:
        st.markdown(
            "##### Selected tool"
        )

        st.markdown(
            (
                '<span class="tool-badge">'
                + result[
                    "tool_label"
                ]
                + "</span>"
            ),
            unsafe_allow_html=True,
        )

    with info_col2:
        st.metric(
            "Agent latency",
            f"{result['latency_seconds']:.1f} s",
        )

    # --------------------------------------------------------
    # Final answer
    # --------------------------------------------------------

    st.markdown(
        "### Answer"
    )

    st.markdown(
        '<div class="answer-card">',
        unsafe_allow_html=True,
    )

    st.markdown(
        result[
            "answer"
        ]
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True,
    )

    # --------------------------------------------------------
    # Sources
    # --------------------------------------------------------

    st.markdown(
        "### Official SFOE sources"
    )

    sources = result.get(
        "sources",
        [],
    )

    if not sources:
        st.info(
            "No source citations were returned for this answer."
        )

    else:
        for source in sources:

            source_number = (
                source.get(
                    "number",
                    "?"
                )
            )

            title = (
                source.get(
                    "title"
                )
                or "SFOE publication"
            )

            published_at = (
                source.get(
                    "published_at"
                )
            )

            download_url = (
                source.get(
                    "download_url"
                )
            )

            st.markdown(
                '<div class="source-card">',
                unsafe_allow_html=True,
            )

            st.markdown(
                f"**[{source_number}] {title}**"
            )

            if published_at:
                st.caption(
                    f"Published: {published_at}"
                )

            if download_url:
                st.link_button(
                    "Open SFOE publication",
                    download_url,
                )

            st.markdown(
                "</div>",
                unsafe_allow_html=True,
            )

    # --------------------------------------------------------
    # Agent details
    # --------------------------------------------------------

    with st.expander(
        "Agent details",
        expanded=False,
    ):

        st.markdown(
            "**Selected MCP tool**"
        )

        st.code(
            result[
                "tool_name"
            ],
            language=None,
        )

        st.markdown(
            "**Routing reason**"
        )

        st.write(
            result[
                "routing_reason"
            ]
        )

        st.markdown(
            "**Tool arguments**"
        )

        st.json(
            result[
                "arguments"
            ]
        )

        st.markdown(
            "**Automatic repair used**"
        )

        st.write(
            (
                "Yes"
                if result[
                    "repair_attempted"
                ]
                else "No"
            )
        )

        st.markdown(
            "**Agent latency**"
        )

        st.write(
            f"{result['latency_seconds']:.2f} seconds"
        )


# ============================================================
# 14. FOOTER
# ============================================================

st.divider()

st.caption(
    "Official energy information is retrieved from the "
    "SFOE Open Energy Knowledge Gateway. "
    "Answers are generated from retrieved evidence and may "
    "include estimated or projected values where indicated "
    "by the source data."
)
