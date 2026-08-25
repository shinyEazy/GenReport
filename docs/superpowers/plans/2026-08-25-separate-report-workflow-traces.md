# Separate Report Discovery and Generation Traces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit two independent LangSmith root traces: the familiar discovery
tree and a generation tree containing only model and tool spans.

**Architecture:** Run input preparation before entering the traced generation
async generator. `DiscoveryAgent` remains independently traced as
`genreport-file-discovery`; `ReportExecutionService` then traces only the LLM
rounds and executor calls below `genreport-report-workflow`. Preserve every
existing SSE event, failure mapping, gateway event, and cleanup behavior.

**Tech Stack:** Python 3.11, FastAPI/SSE, async generators, LangSmith
`traceable`, unittest.

---

## File map

- Modify `backend/app/services/report_file_discovery.py`: restore the
  discovery root operation name.
- Modify `backend/app/services/report_execution.py`: move input preparation
  outside the remote workflow root and retain only model/tool child wrappers.
- Modify `backend/tests/test_report_file_discovery.py`: lock the discovery
  root trace contract.
- Modify `backend/tests/test_report_execution.py`: lock the separate,
  model/tool-only generation tree and preparation failure behavior.

### Task 1: Restore the independent discovery trace contract

**Files:**
- Modify: `backend/tests/test_report_file_discovery.py:61-75`
- Modify: `backend/app/services/report_file_discovery.py:258-266`

- [ ] **Step 1: Change the discovery trace-factory expectation to the legacy root**

  Replace the existing assertion with:

  ```python
  trace_operation.assert_called_once_with(
      operation,
      name="genreport-file-discovery",
      run_type="chain",
      tags=["genreport", "file-discovery"],
  )
  ```

- [ ] **Step 2: Verify the test fails for the expected name mismatch**

  Run:

  ```bash
  cd backend
  ../.venv/bin/python -m unittest \
    tests.test_report_file_discovery.DiscoveryAgentTests.test_trace_helper_delegates_to_shared_trace_operation -v
  ```

  Expected: the mock was called with `name="file-discovery"` rather than
  `name="genreport-file-discovery"`.

- [ ] **Step 3: Restore the discovery operation name**

  In `_trace_discovery_call()`, change only the `trace_operation()` name:

  ```python
  return trace_operation(
      function,
      name="genreport-file-discovery",
      run_type="chain",
      tags=["genreport", "file-discovery"],
  )
  ```

  Keep the shared payload processors and tags unchanged.

- [ ] **Step 4: Verify the discovery tracing tests pass**

  Run:

  ```bash
  cd backend
  ../.venv/bin/python -m unittest \
    tests.test_report_file_discovery.DiscoveryAgentTests.test_trace_helper_delegates_to_shared_trace_operation \
    tests.test_report_file_discovery.DiscoveryAgentTests.test_discovery_runs_through_injected_trace_wrapper -v
  ```

  Expected: both tests pass.

- [ ] **Step 5: Commit the discovery contract**

  ```bash
  git add backend/app/services/report_file_discovery.py \
    backend/tests/test_report_file_discovery.py
  git commit -m "fix: restore independent discovery trace"
  ```

### Task 2: Separate preparation from the generation root

**Files:**
- Modify: `backend/tests/test_report_execution.py:241-272`
- Modify: `backend/app/services/report_execution.py:84-163`

- [ ] **Step 1: Replace the remote trace-shape test with the compact contract**

  Rename the test to
  `test_remote_workflow_traces_only_model_and_tool_rounds` and assert:

  ```python
  self.assertEqual(
      [call[0] for call in trace_calls],
      [
          "genreport-report-workflow",
          "model",
          "tools",
          "model",
      ],
  )
  ```

  Continue to run it with `ToolCallingLLM`, `FakeExecutor`, and
  `RecordingRuntimeGatewayClient`, and keep the assertion that the final event
  is `report.completed`.

- [ ] **Step 2: Verify the compact-contract test fails**

  Run:

  ```bash
  cd backend
  ../.venv/bin/python -m unittest \
    tests.test_report_execution.ReportExecutionTests.test_remote_workflow_traces_only_model_and_tool_rounds -v
  ```

  Expected: the trace list still includes preparation, asset materialization,
  prompt construction, and artifact finalization.

- [ ] **Step 3: Move input preparation before the root generation trace**

  Refactor `ReportExecutionService.stream()` so it:

  ```python
  async def stream(self, request: ReportExecutionRequest) -> AsyncIterator[ReportEvent]:
      event_factory = self.event_factory_builder(request)
      yield event_factory.create(
          "report.status",
          {"phase": "preparing", "message": "Preparing report execution."},
      )
      try:
          prepared_inputs = await self._prepare_inputs_impl(request=request)
      except ReportInputPreparationError as exc:
          failure = ReportFailure(
              code="report_input_preparation_failed",
              phase="discovery",
              message=str(exc),
              retryable=True,
          )
          yield event_factory.create("report.failed", failure.model_dump(mode="json"))
          return

      effective_request = request.model_copy(
          update={"execution_files": prepared_inputs.files}
      )
      async for event in self._stream_traced(
          request=effective_request,
          selected_inputs=prepared_inputs.selected_inputs,
      ):
          yield event
  ```

  Move the remaining generation body into `_stream_impl()`. It must accept
  `request` and `selected_inputs`, create its `ReportEventFactory`, materialize
  assets, emit `report.inputs.selected`, then retain the existing LLM loop,
  failure mapping, and executor close in `finally`.

  Do not wrap preparation with `trace_operation`; this lets the independent
  `DiscoveryAgent` trace remain a LangSmith root instead of a child.

- [ ] **Step 4: Verify unchanged event and preparation-failure behavior**

  Run:

  ```bash
  cd backend
  ../.venv/bin/python -m unittest \
    tests.test_report_execution.ReportExecutionTests.test_streams_delta_usage_and_completion \
    tests.test_report_execution.ReportExecutionTests.test_executor_closes_after_model_failure -v
  ```

  Expected: both pass; the status and selected-input events retain their
  original positions and executor cleanup remains intact.

### Task 3: Retain only model and tool child spans

**Files:**
- Modify: `backend/app/services/report_execution.py:90-125, 154-377, 412-478`
- Modify: `backend/tests/test_report_execution.py:241-272`

- [ ] **Step 1: Remove non-agent trace wrappers**

  Delete the pre-wrapped fields and helper methods for input preparation, asset
  materialization, prompt construction, and artifact finalization. Invoke their
  existing behavior directly:

  ```python
  await executor.materialize_assets()
  messages = build_report_messages(
      request,
      available_files=executor.get_available_files_prompt(),
      image_parts=image_parts,
  )
  artifacts = await executor.finalize_generated_files(
      generated_files,
      workspace_id=request.workspace_id,
  )
  ```

- [ ] **Step 2: Rename the remaining child wrappers**

  Keep the async-generator LLM wrapper and executor-tool wrapper, but configure
  them as:

  ```python
  self._stream_llm_round_traced = trace_operation(
      self._stream_llm_round_impl,
      name="model",
      run_type="llm",
      tags=[*REMOTE_WORKFLOW_TAGS, "model"],
  )
  self._execute_tool_traced = trace_operation(
      self._execute_tool_impl,
      name="tools",
      run_type="tool",
      tags=[*REMOTE_WORKFLOW_TAGS, "tools"],
  )
  ```

- [ ] **Step 3: Verify the compact trace test passes**

  Run:

  ```bash
  cd backend
  ../.venv/bin/python -m unittest \
    tests.test_report_execution.ReportExecutionTests.test_remote_workflow_traces_only_model_and_tool_rounds -v
  ```

  Expected: trace calls are exactly workflow → model → tools → model, and the
  SSE stream completes.

- [ ] **Step 4: Run the remote execution module**

  Run:

  ```bash
  cd backend
  ../.venv/bin/python -m unittest tests.test_report_execution -v
  ```

  Expected: all tests in this module pass.

- [ ] **Step 5: Commit the generation tree change**

  ```bash
  git add backend/app/services/report_execution.py \
    backend/tests/test_report_execution.py
  git commit -m "refactor: separate report generation trace"
  ```

### Task 4: Verify the two-root trace contract

**Files:**
- Test: `backend/tests/test_report_file_discovery.py`
- Test: `backend/tests/test_report_execution.py`

- [ ] **Step 1: Run focused trace tests**

  ```bash
  cd backend
  ../.venv/bin/python -m unittest \
    tests.test_report_file_discovery.DiscoveryAgentTests.test_trace_helper_delegates_to_shared_trace_operation \
    tests.test_report_file_discovery.DiscoveryAgentTests.test_discovery_runs_through_injected_trace_wrapper \
    tests.test_report_execution \
    tests.test_report_tracing -v
  ```

  Expected: all selected tests pass. The recorded discovery and generation
  wrappers are configured independently.

- [ ] **Step 2: Compile and check whitespace**

  ```bash
  cd backend
  ../.venv/bin/python -m compileall -q app tests
  cd ..
  git diff --check
  ```

  Expected: both commands exit successfully.

- [ ] **Step 3: Run the complete backend suite**

  ```bash
  cd backend
  ../.venv/bin/python -m unittest discover -s tests -v
  ```

  Expected: the known baseline failures remain limited to the discovery call
  limit, prompt wording, and two stateless-architecture checks; no tracing
  regression fails.
