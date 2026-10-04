(function () {
  "use strict";

  var SNAPSHOT_URL = "./data/snapshot.json";
  var replayTimer = null;
  var running = false;
  var runGen = 0;
  var snapshot = null;
  var filter = "ALL";
  var logStarted = 0;

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function show(value) {
    if (value === null || value === undefined || value === "") return "pending";
    return String(value);
  }

  function decisionLabel(decision) {
    if (decision === "RETAIN") return "RETAINED BY CALIBRATED SCREENING";
    if (decision === "DEPRIORITIZE") return "DEPRIORITIZE";
    return show(decision);
  }

  function fmt(value, digits) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "pending";
    return Number(value).toFixed(digits);
  }

  function el(id) {
    return document.getElementById(id);
  }

  function localHost() {
    var host = location.hostname;
    return location.protocol === "file:" || host === "" || host === "localhost" || host === "127.0.0.1";
  }

  function statusClass(status) {
    var s = String(status || "").toUpperCase();
    if (s === "VETO") return "veto";
    if (s === "APPROVE") return "approve";
    if (s.indexOf("BLOCK") >= 0) return "blocked";
    return "pending";
  }

  function fail(message) {
    el("page").hidden = true;
    el("missing").hidden = false;
    el("missing").textContent = message;
    el("caveat-bar").textContent = message;
  }

  function logLine(text) {
    var list = el("run-log");
    if (!list) return;
    var t = ((performance.now() - logStarted) / 1000).toFixed(1);
    var li = document.createElement("li");
    li.innerHTML = '<span class="t">' + esc(t + "s") + "</span>" + esc(text);
    list.appendChild(li);
    list.scrollTop = list.scrollHeight;
  }

  function clearLog() {
    logStarted = performance.now();
    el("run-log").innerHTML = "";
  }

  function captions(data) {
    var tests = (data.planner && data.planner.tests) || [];
    var dft = tests.filter(function (t) { return t.name === "dft_validation"; })[0];
    var dftWhy = dft && dft.runnable_in_this_lab === false ? "not runnable" : "not chosen";
    var n = data.result ? data.result.n_screened : "pending";
    var controls = data.result && data.result.controls_passed ? "passed" : "failed";
    var verdict = data.evaluation ? data.evaluation.verdict : "pending";
    var next = data.learning ? data.learning.next_decision : "pending";
    var audit = data.auditor ? data.auditor.audit_status : "pending";
    var gate = data.human_gate ? data.human_gate.status : "pending";
    return [
      "Hypothesis registered before screening",
      "Planner rejected dft_validation (" + dftWhy + ") and composition_proxy (wrong construct)",
      "Runner screened " + n + ", controls " + controls,
      "Analyst verdict " + verdict + ", next_decision " + next,
      "Auditor " + show(audit),
      "Human gate " + show(gate),
      "Iteration 2 blocked pending held-out validation."
    ];
  }

  function hullScale(data) {
    var max = Number(data.result && data.result.cutoff_ev_per_atom) || 0;
    (data.candidates || []).forEach(function (c) {
      var h = Number(c.chgnet_hull_ev_per_atom);
      if (h > max) max = h;
    });
    return max > 0 ? max : 1;
  }

  function pct(value, scale) {
    var n = Number(value);
    if (!isFinite(n) || !scale) return 0;
    return Math.max(0, Math.min(100, (n / scale) * 100));
  }

  function sourced(metrics, path) {
    var node = path.split(".").reduce(function (obj, key) {
      return obj && obj[key];
    }, metrics);
    if (!node || typeof node !== "object" || !("value" in node)) return "";
    return "<div><strong>" + esc(node.value) + "</strong> <span class=\"quiet\">" +
      esc(path) + " · " + esc(node.source || "") + "</span></div>";
  }

  function renderHeader(data) {
    el("caveat-bar").textContent = data.caveat;
    el("footer-caveat").textContent = data.caveat;
    el("kicker").textContent = "Omnigent lab · " + (data.evidence_tier || "T1") + " CHGNet triage · in-sample";
    el("question-text").textContent = data.question;
    var repo = "";
    if (!localHost() && data.repo_url) {
      repo = ' · <a href="' + esc(data.repo_url) + '">GitHub</a>';
    }
    el("header-meta").innerHTML = "Evidence tier " + esc(data.evidence_tier || "T1") +
      " · run " + esc(data.run_id) + repo;
    var gate = data.human_gate || {};
    var audit = data.auditor || {};
    var it = data.iteration2 || {};
    var tiles = [
      ["Run", data.run_id],
      ["Verdict", data.evaluation && data.evaluation.verdict],
      ["Auditor", audit.audit_status],
      ["Human gate", gate.status],
      ["Next experiment", (it.next_experiment || "pending") + " · " + (it.experiment_status || it.status || "pending")]
    ];
    el("status-tiles").innerHTML = tiles.map(function (tile) {
      return '<article class="tile"><span class="label">' + esc(tile[0]) +
        "</span><strong>" + esc(show(tile[1])) + "</strong></article>";
    }).join("");
  }

  function renderTimeline(data) {
    var ids = data.ids || {};
    var pi = (data.agents || []).filter(function (a) { return a.id === "pi"; })[0];
    el("pi-banner").textContent = (pi ? pi.name : "PI") + " delegates; copies IDs only. Agents run in order.";
    var steps = [
      ["Hypothesis", "hypothesis_id", ids.hypothesis_id],
      ["Planner", "spec_id", ids.spec_id],
      ["Runner", "run_id", ids.run_id],
      ["Analyst", "evaluation_run_id", ids.evaluation_run_id],
      ["Auditor", "audit_id", ids.audit_id],
      ["Human gate", "approval_id", ids.approval_id],
      ["Iteration 2", "plan_id", ids.plan_id]
    ];
    var text = captions(data);
    el("timeline").innerHTML = steps.map(function (step, i) {
      return '<article class="node" data-step="' + i + '" data-caption="' + esc(text[i]) + '">' +
        "<strong>" + esc(step[0]) + "</strong>" +
        '<span class="id">' + esc(step[1]) + " " + esc(show(step[2])) + "</span></article>";
    }).join("");
  }

  function renderPlanner(data) {
    var planner = data.planner || {};
    el("planner-choice").innerHTML =
      "<p>Chosen test <strong>" + esc(show(planner.chosen_test)) + "</strong> · " +
      "max_candidates upper bound " + esc(show(planner.max_candidates_upper_bound)) +
      " · unknowns " + esc(planner.unknowns == null ? "pending" : planner.unknowns) + ".</p>" +
      "<p><strong>Alternatives.</strong> " + esc(planner.alternatives_considered || "") + "</p>" +
      "<p><strong>Expected learning.</strong> " + esc(planner.expected_learning || "") + "</p>" +
      "<p><strong>Stop rule.</strong> " + esc(planner.stop_rule || "") + "</p>";
    var highlights = {
      composition_proxy: ["measured_performance.auroc", "measured_performance.enrichment"],
      chgnet_triage: [
        "deployed_policy_in_sample.screening_cutoff_ev_per_atom",
        "deployed_policy_in_sample.precision",
        "deployed_policy_in_sample.recall"
      ],
      dft_validation: []
    };
    el("test-cards").innerHTML = (planner.tests || []).map(function (test) {
      var chosen = test.name === planner.chosen_test;
      var why = chosen ? test.why_chosen : test.why_not_chosen;
      var bits = (highlights[test.name] || []).map(function (path) {
        return sourced(test.metrics, path);
      }).join("");
      if (test.name === "dft_validation" && test.metrics && test.metrics.note) {
        bits = "<p>" + esc(test.metrics.note) + "</p>";
      }
      return '<article class="card' + (chosen ? " chosen" : "") + '">' +
        "<h3>" + esc(test.name) + (chosen ? " · chosen" : "") + "</h3>" +
        "<p class=\"quiet\">tier " + esc(show(test.tier)) + " · runnable " +
        esc(String(test.runnable_in_this_lab)) + "</p>" +
        "<p>" + esc(why || "pending") + "</p>" + bits + "</article>";
    }).join("");

    var budget = data.budget || {};
    var wall = data.result ? data.result.wall_ms : null;
    var note = data.result ? data.result.timing_note : "";
    el("budget").innerHTML =
      "<h3>Human budget " + esc(show(budget.budget_id)) + "</h3>" +
      '<div class="meter" aria-hidden="true"><span></span></div>' +
      "<p>total_compute_budget_s <strong>" + esc(show(budget.total_compute_budget_s)) +
      "</strong> · set_by " + esc(show(budget.set_by)) +
      " · budget card pool " + esc(show(budget.candidate_pool_size)) + ".</p>" +
      "<p><strong>affordable_candidates UPPER BOUND " + esc(show(budget.affordable_candidates)) +
      ".</strong> " + esc(budget.cost_basis_note || "") + "</p>" +
      "<p class=\"quiet\">This recorded run is a cache lookup, wall_ms " +
      esc(wall == null ? "pending" : fmt(wall, 3)) + ". " + esc(note || "") +
      " The bar is the human budget, not a spend meter.</p>";
  }

  function renderResult(data) {
    var result = data.result || {};
    var scale = hullScale(data);
    var retained = Number(result.n_retained) || 0;
    var dropped = Number(result.n_deprioritized) || 0;
    var total = retained + dropped || 1;
    el("funnel").innerHTML =
      '<div class="funnel-pool">Screened ' + esc(show(result.n_screened)) +
      " of pool " + esc(show(result.n_pool)) + "</div>" +
      '<div class="funnel-split">' +
      '<div class="funnel-arm retain" style="flex:' + retained + '"><span>' + esc(show(result.n_retained)) +
      "</span><small>RETAINED BY CALIBRATED SCREENING</small></div>" +
      '<div class="funnel-arm deprio" style="flex:' + dropped + '"><span>' + esc(show(result.n_deprioritized)) +
      "</span><small>DEPRIORITIZE</small></div></div>" +
      "<p class=\"quiet\">Validation queue " + esc(show(result.validation_queue_size)) +
      ". Widths follow " + retained + " and " + dropped + " out of " + (retained + dropped || total) + ".</p>";

    var cutoff = Number(result.cutoff_ev_per_atom);
    var ticks = (data.candidates || []).map(function (c) {
      var cls = c.screening_decision === "RETAIN" ? "retain" : "deprio";
      return '<i class="tick ' + cls + '" style="left:' + pct(c.chgnet_hull_ev_per_atom, scale) +
        '%" title="' + esc(c.material_id + " " + c.chgnet_hull_ev_per_atom) + '"></i>';
    }).join("");
    el("strip-wrap").innerHTML =
      '<div class="strip" aria-hidden="true">' + ticks +
      '<i class="cutoff-mark" style="left:' + pct(cutoff, scale) + '%"></i></div>' +
      '<p class="strip-label quiet">Each mark is one candidate hull. Blue is retained, gray is deprioritized. ' +
      "The amber line is the screening cutoff (" + esc(show(result.cutoff_ev_per_atom)) +
      " eV/atom). Positions use the highest hull in this snapshot as the right edge.</p>";

    var items = [
      [result.n_screened, "screened"],
      [result.n_retained, "RETAINED BY CALIBRATED SCREENING"],
      [result.n_deprioritized, "DEPRIORITIZE"],
      [result.validation_queue_size, "validation queue"]
    ];
    el("big-numbers").innerHTML = items.map(function (item) {
      return '<div class="stat"><span class="num">' + esc(show(item[0])) +
        '</span><span class="label">' + esc(item[1]) + "</span></div>";
    }).join("");
    el("result-note").textContent = "Frozen convention " + show(result.frozen_convention) +
      ". Controls " + (result.controls_passed ? "passed" : "failed") + ".";
    el("controls").innerHTML = (result.controls || []).map(function (c) {
      var ok = c.passed === true;
      return "<li class=\"" + (ok ? "pass" : "fail") + "\">" + esc(c.control_id) +
        " · " + (ok ? "pass" : "fail") + "</li>";
    }).join("");
    var top = result.validation_queue_top5 || [];
    el("timing").textContent = "wall_ms " + (result.wall_ms == null ? "pending" : fmt(result.wall_ms, 3)) +
      ". " + (result.timing_note || "") +
      (top.length ? " Queue head: " + top.join(", ") + "." : "");
  }

  function heatCell(label, count, maxCount) {
    var n = Number(count);
    var alpha = maxCount ? (0.12 + 0.55 * (n / maxCount)) : 0.15;
    return '<div class="heat-cell" style="--ink-a:' + alpha.toFixed(3) + '"><b>' +
      esc(show(count)) + "</b><span>" + esc(label) + "</span></div>";
  }

  function renderMatrix(data) {
    var m = (data.evaluation && data.evaluation.retrospective_mp_metrics) || {};
    var inSample = data.evaluation && data.evaluation.in_sample;
    el("matrix-caption").textContent = (inSample ? "In-sample, not prospective. " : "") +
      "Column " + show(m.mp_hull_column) + ". " + (m.warning || "");
    var maxCount = Math.max(Number(m.tp) || 0, Number(m.fp) || 0, Number(m.fn) || 0, Number(m.tn) || 0, 1);
    el("matrix-table").innerHTML =
      '<div class="heat">' +
      '<div></div><div class="h">MP hull ≤ 0.05</div><div class="h">MP hull &gt; 0.05</div>' +
      '<div class="h">RETAINED BY CALIBRATED SCREENING</div>' +
      heatCell("TP", m.tp, maxCount) + heatCell("FP", m.fp, maxCount) +
      '<div class="h">DEPRIORITIZE</div>' +
      heatCell("FN", m.fn, maxCount) + heatCell("TN", m.tn, maxCount) +
      "</div><p>precision " + esc(fmt(m.precision, 3)) + " · recall " + esc(fmt(m.recall, 3)) + "</p>";
    var p = data.policy_counts || {};
    el("policy-counts").innerHTML =
      "<h3>stability_policy.json</h3>" +
      "<p>" + esc(data.policy_vs_run_warning || "") + "</p>" +
      "<p>Run TP " + esc(show(m.tp)) + " FP " + esc(show(m.fp)) + " FN " + esc(show(m.fn)) +
      " TN " + esc(show(m.tn)) + "</p>" +
      "<p>Policy TP " + esc(show(p.tp)) + " FP " + esc(show(p.fp)) + " FN " + esc(show(p.fn)) +
      " TN " + esc(show(p.tn)) + "</p>" +
      "<p class=\"quiet\">precision_at_cutoff " + esc(show(p.precision_at_cutoff)) +
      " · recall_at_cutoff " + esc(show(p.recall_at_cutoff)) + "</p>";
  }

  function renderLearning(data) {
    var ev = data.evaluation || {};
    var learn = data.learning || {};
    var pred = ev.predicted || {};
    var obs = ev.observed || {};
    el("learning-body").innerHTML =
      '<div class="compare">' +
      '<article class="card"><h3>Predicted</h3><p>pool reduction ' + esc(show(pred.pool_reduction)) +
      "</p><p>recall of MP-stable " + esc(show(pred.recall_of_mp_stable)) + "</p></article>" +
      '<article class="card"><h3>Observed</h3><p>pool reduction ' + esc(show(obs.pool_reduction)) +
      "</p><p>recall of MP-stable " + esc(show(obs.recall_of_mp_stable)) + "</p></article></div>" +
      "<p><strong>Verdict (computed)</strong> " + esc(show(ev.verdict)) + ".</p>" +
      "<p><strong>Next decision</strong> " + esc(show(learn.next_decision)) +
      " · signal " + esc(show(learn.deterministic_signal)) +
      " · match " + esc(show(learn.matches_signal)) + ".</p>" +
      "<p>" + esc(learn.rationale || "") + "</p>" +
      "<p>" + esc(learn.what_was_learned || "") + "</p>" +
      "<p><strong>Strategy rule</strong> " + esc(show(learn.proposed_rule_change)) +
      " · applied: " + esc(String(learn.rule_applied)) + ".</p>";
  }

  function renderGate(data) {
    var a = data.auditor || {};
    var checks = (a.checks_failed || []).map(function (c) { return "<li>" + esc(c) + "</li>"; }).join("");
    var warnings = (a.warnings || []).map(function (c) { return "<li>" + esc(c) + "</li>"; }).join("");
    el("auditor").innerHTML =
      '<p><span class="pill ' + statusClass(a.audit_status) + '">' + esc(show(a.audit_status)) + "</span> " +
      "audit_id " + esc(show(a.audit_id)) + " · veto " + esc(show(a.veto)) +
      " · promotion_allowed " + esc(show(a.promotion_allowed)) + "</p>" +
      "<p>" + esc(a.reason || "") + "</p>" +
      "<p><strong>checks_failed</strong></p><ul>" + (checks || "<li>pending</li>") + "</ul>" +
      "<p><strong>warnings</strong></p><ul>" + (warnings || "<li>none</li>") + "</ul>";
    var g = data.human_gate || {};
    el("human-gate").innerHTML =
      "<h3>Human gate <span class=\"pill " + statusClass(g.status) + "\">" + esc(show(g.status)) + "</span></h3>" +
      "<p>approver " + esc(show(g.approver)) + " · timestamp " + esc(show(g.timestamp)) +
      " · approval_id " + esc(show(g.approval_id)) + " · channel " + esc(show(g.channel)) + "</p>" +
      "<p>Approval is CLI-only: <code>uv run python scripts/human_gate.py --run-id " +
      esc(data.run_id) + "</code></p>";
  }

  function renderNext(data) {
    var it = data.iteration2 || {};
    var experimentStatus = it.experiment_status || (it.plan_id == null ? "blocked" : it.status);
    el("next-body").innerHTML =
      "<p><strong>" + esc(show(it.next_experiment)) + "</strong> " +
      '<span class="pill ' + statusClass(experimentStatus) + '">' + esc(show(experimentStatus)) + "</span></p>" +
      "<p>" + esc(it.reason || "") + "</p>" +
      "<p class=\"quiet\">Iteration-2 plan " + esc(show(it.status)) +
      " · plan_id " + esc(show(it.plan_id)) +
      " · focus " + esc(show(it.focus)) +
      " · focus_size " + esc(show(it.focus_size)) +
      " · previous_pool_size " + esc(show(it.previous_pool_size)) + ".</p>";
  }

  function sortedCandidates(data) {
    return (data.candidates || []).slice().sort(function (a, b) {
      var av = Number(a.validation_priority);
      var bv = Number(b.validation_priority);
      if (av !== bv) return av - bv;
      return String(a.material_id).localeCompare(String(b.material_id));
    });
  }

  function renderCandidateTable() {
    if (!snapshot) return;
    var scale = hullScale(snapshot);
    var cutoff = snapshot.result ? snapshot.result.cutoff_ev_per_atom : null;
    var q = (el("search").value || "").trim().toLowerCase();
    var rows = sortedCandidates(snapshot).filter(function (c) {
      if (filter === "RETAIN" && c.screening_decision !== "RETAIN") return false;
      if (filter === "DEPRIORITIZE" && c.screening_decision !== "DEPRIORITIZE") return false;
      if (!q) return true;
      return String(c.formula || "").toLowerCase().indexOf(q) >= 0 ||
        String(c.material_id || "").toLowerCase().indexOf(q) >= 0;
    });
    el("candidate-rows").innerHTML = rows.map(function (c) {
      var cls = c.screening_decision === "RETAIN" ? "retain" : "deprio";
      return "<tr><td>" + esc(c.queue_rank == null ? "—" : c.queue_rank) + "</td><td>" +
        esc(c.material_id) + "</td><td>" + esc(c.formula) + "</td><td class=\"hull-cell\">" +
        '<div class="track"><div class="fill ' + cls + '" style="width:' +
        pct(c.chgnet_hull_ev_per_atom, scale).toFixed(2) + '%"></div>' +
        '<i class="mark" style="left:' + pct(cutoff, scale).toFixed(2) + '%"></i></div>' +
        '<span class="hull-num">' + esc(c.chgnet_hull_ev_per_atom) + "</span></td><td>" +
        '<span class="pill ' + cls + '">' + esc(decisionLabel(c.screening_decision)) + "</span></td></tr>";
    }).join("");
    el("candidate-count").textContent = rows.length + " shown · sorted by validation_priority ascending. " +
      "Bar length is the hull relative to the highest hull in the snapshot. The mark is the cutoff.";
  }

  function renderCandidates(data) {
    el("priority-note").textContent = data.priority_note || "";
    el("filters").innerHTML = ["ALL", "RETAIN", "DEPRIORITIZE"].map(function (name) {
      return '<button type="button" class="chip" data-filter="' + name + '" aria-pressed="' +
        (name === filter ? "true" : "false") + '">' + name + "</button>";
    }).join("");
    renderCandidateTable();
  }

  function renderArmA(data) {
    var arm = data.arm_a || {};
    var pe = arm.point_estimates || {};
    function row(name) {
      var r = pe[name] || {};
      var width = Math.max(0, Math.min(100, (Number(r.auroc) || 0) * 100));
      return '<div class="bar-row"><span>' + esc(name) + '</span><div class="bar-track">' +
        '<div class="bar-fill" style="width:' + width.toFixed(2) + '%"></div>' +
        '<i class="bar-mid" title="0.5"></i></div><span>' + esc(fmt(r.auroc, 3)) + "</span></div>";
    }
    var verdicts = arm.verdicts_summary || {};
    var verdictHtml = Object.keys(verdicts).map(function (key) {
      var v = verdicts[key] || {};
      return "<li><strong>" + esc(key) + "</strong> " + esc(v.verdict) + " — " + esc(v.text || "") + "</li>";
    }).join("");
    function rateTable() {
      return ["proxy", "B0", "B1"].map(function (name) {
        var r = pe[name] || {};
        return "<tr><th>" + esc(name) + "</th><td>" + esc(fmt(r.auroc, 3)) + "</td><td>" +
          esc(fmt(r.hit_rate_top10, 3)) + "</td><td>" + esc(fmt(r.enrichment, 3)) + "</td></tr>";
      }).join("");
    }
    el("arma-body").innerHTML =
      "<p class=\"quiet\">n_samples " + esc(show(arm.n_samples)) + " · n_positive " +
      esc(show(arm.n_positive)) + " · base_rate " + esc(fmt(arm.base_rate, 3)) +
      ". Bars are AUROC. The amber tick is 0.5.</p>" +
      '<div class="bars">' + row("proxy") + row("B0") + row("B1") + "</div>" +
      "<table><thead><tr><th></th><th>AUROC</th><th>hit rate top 10</th><th>enrichment</th></tr></thead><tbody>" +
      rateTable() + "</tbody></table>" +
      "<ul>" + verdictHtml + "</ul>" +
      "<p><strong>" + esc(arm.honest_note || "") + "</strong></p>";
  }

  function renderTrace(data) {
    var ids = data.ids || {};
    var keys = ["hypothesis_id", "spec_id", "run_id", "evaluation_run_id", "audit_id",
      "approval_id", "plan_id", "policy_calibration_run_id"];
    el("ids").innerHTML = keys.map(function (key) {
      return "<dt>" + esc(key) + "</dt><dd>" + esc(show(ids[key])) + "</dd>";
    }).join("") + "<dt>candidates_source</dt><dd>" + esc(data.candidates_source || "") + "</dd>";
    var reports = data.reports || {};
    el("discovery-md").textContent = reports.discovery_markdown || "";
    el("iteration-md").textContent = reports.iteration_markdown || "";
  }

  function renderAll(data) {
    el("page").hidden = false;
    el("missing").hidden = true;
    renderHeader(data);
    renderTimeline(data);
    renderPlanner(data);
    renderResult(data);
    renderMatrix(data);
    renderLearning(data);
    renderGate(data);
    renderNext(data);
    renderCandidates(data);
    renderArmA(data);
    renderTrace(data);
  }

  function stopRun() {
    runGen += 1;
    running = false;
    if (replayTimer) {
      clearInterval(replayTimer);
      replayTimer = null;
    }
    el("run-btn").textContent = "Run one discovery";
    el("run-btn").setAttribute("aria-pressed", "false");
    el("log-state").textContent = "idle";
    Array.prototype.forEach.call(document.querySelectorAll(".node"), function (node) {
      node.classList.remove("active");
    });
  }

  function playSteps(data, gen) {
    var nodes = document.querySelectorAll(".node");
    var i = 0;
    function step() {
      if (gen !== runGen) return;
      Array.prototype.forEach.call(nodes, function (node) { node.classList.remove("active"); });
      if (i >= nodes.length) {
        clearInterval(replayTimer);
        replayTimer = null;
        running = false;
        el("run-btn").textContent = "Run one discovery";
        el("run-btn").setAttribute("aria-pressed", "false");
        el("log-state").textContent = "recorded run " + data.run_id;
        el("replay-caption").textContent = "Recorded handoff finished. No new screening was computed.";
        logLine("Handoff complete. Human gate " + show(data.human_gate && data.human_gate.status) +
          ". Iteration 2 plan " + show(data.iteration2 && data.iteration2.plan_id) + ".");
        return;
      }
      nodes[i].classList.add("active");
      nodes[i].classList.add("done");
      var caption = nodes[i].getAttribute("data-caption") || "";
      el("replay-caption").textContent = caption;
      logLine(caption);
      i += 1;
    }
    step();
    replayTimer = setInterval(step, 8000 / 7);
  }

  function loadSnapshot() {
    return fetch(SNAPSHOT_URL).then(function (res) {
      if (!res.ok) throw new Error("missing");
      return res.json();
    });
  }

  function startRun() {
    if (running) {
      stopRun();
      logLine("Stopped.");
      return;
    }
    running = true;
    var gen = ++runGen;
    el("run-btn").textContent = "Stop";
    el("run-btn").setAttribute("aria-pressed", "true");
    el("log-state").textContent = "loading";
    clearLog();
    logLine("GET " + SNAPSHOT_URL);
    el("replay-caption").textContent = "Loading the recorded discovery…";
    var handoff = document.getElementById("handoff");
    if (handoff && handoff.scrollIntoView) handoff.scrollIntoView({ behavior: "smooth", block: "start" });
    loadSnapshot().then(function (data) {
      if (gen !== runGen) return;
      snapshot = data;
      renderAll(data);
      el("log-state").textContent = "playing";
      logLine("200 · run_id " + data.run_id + " · " +
        ((data.candidates && data.candidates.length) || 0) + " candidates · source " +
        (data.candidates_source || "snapshot"));
      logLine("PI delegates. Walking hypothesis → planner → runner → analyst → auditor → human gate → iteration 2.");
      playSteps(data, gen);
    }).catch(function () {
      if (gen !== runGen) return;
      running = false;
      el("run-btn").textContent = "Run one discovery";
      el("run-btn").setAttribute("aria-pressed", "false");
      el("log-state").textContent = "error";
      logLine("snapshot missing — run scripts/export_demo_snapshot.py");
      if (!snapshot) fail("snapshot missing — run scripts/export_demo_snapshot.py");
    });
  }

  el("filters").addEventListener("click", function (event) {
    var btn = event.target.closest("[data-filter]");
    if (!btn) return;
    filter = btn.getAttribute("data-filter");
    Array.prototype.forEach.call(el("filters").querySelectorAll(".chip"), function (chip) {
      chip.setAttribute("aria-pressed", chip === btn ? "true" : "false");
    });
    renderCandidateTable();
  });
  el("search").addEventListener("input", renderCandidateTable);
  el("run-btn").addEventListener("click", startRun);

  loadSnapshot().then(function (data) {
    snapshot = data;
    renderAll(data);
    logStarted = performance.now();
    logLine("GET " + SNAPSHOT_URL);
    logLine("200 · page ready · run_id " + data.run_id + ". Press Run one discovery to walk the handoff.");
    el("log-state").textContent = "ready";
  }).catch(function () {
    fail("snapshot missing — run scripts/export_demo_snapshot.py");
  });
})();
