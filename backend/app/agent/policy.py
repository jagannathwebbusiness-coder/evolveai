"""Deterministic task/data relevance and observable execution requirements."""

import json
import re

from ..improvement.business_contracts import goal_requirements, PATTERNS, task_intent
from ..tools.errors import ToolError


class TaskPolicy:
    def __init__(self, goal, body):
        self.goal, self.body = goal, body
        self.requirements = goal_requirements(goal)
        self.results = []
        intent = task_intent(goal)
        requested = {
            name for name, pattern in PATTERNS.items() if re.search(pattern, intent)
        }
        if "business_selling_price" in requested:
            requested.discard("business_profit_margin")
        if "business_roi" in requested:
            requested.discard("business_profit")
        self.csv = bool(body.upload_ids) and bool(
            re.search(r"\b(csv|xlsx|excel|workbook|sheet|dataset|uploaded|attached|spreadsheet|data)\b", intent)
        )
        if self.csv and not self.requirements:
            # Words like "revenue" name attached-file columns here, not operands to invent.
            requested = set()
        self.missing_operands = sorted(
            requested - {item["capability"].name for item in self.requirements}
        )
        self.workspace = bool(
            re.search(
                r"\b(list|read|open|inspect|summarize|analyse|analyze)\b[^.!?\n]{0,90}\b(workspace|directory|folder|files?)\b",
                intent,
            )
            or (
                re.search(r"\b(read|open|summarize)\b", intent)
                and re.search(r"\.(?:md|txt|csv|json)\b", goal, re.I)
            )
        )
        self.report = bool(
            re.search(
                r"\b(?:save|create|write|generate|download|produce|export)\b[^.!?\n]{0,70}\breport\b",
                intent,
            )
            or re.search(r"\bcreate_report\b", intent)
        )
        self.paths = set(re.findall(r"[\w./\\-]+\.(?:csv|md|txt|json)\b", goal, re.I))
        self.listed_paths = set()
        self.report_done = False
        self.csv_done = False
        self.trend = self.csv and bool(
            re.search(
                r"\b(trends?|anomal\w*|outliers?|spikes?|unusual|growth|declin\w*|increasing|decreasing|over time)\b",
                intent,
            )
        )
        self.docs = (
            not self.csv
            and not self.requirements
            and bool(
                re.search(
                    r"\b(policy|policies|sops?|handbook|guidelines?|manual|documents?|knowledge|saved notes?|according to (?:our|the|my))\b",
                    intent,
                )
            )
        )
        self.trend_done = self.docs_done = False
        self.read_done = False
        self.list_done = False
        self.arithmetic = self._arithmetic(intent) if not self.requirements else None
        self.arithmetic_done = False
        self.recommendation_count = 0
        if self.requirements and re.search(
            r"\b(recommend|suggest|recommendations)\b", goal, re.I
        ):
            count = re.search(
                r"\b(?:recommend|suggest)\s+(one|two|three|four|five|[1-5])\b",
                goal,
                re.I,
            )
            counts = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
            self.recommendation_count = 3
            if count:
                self.recommendation_count = (
                    int(count[1]) if count[1].isdigit() else counts[count[1].lower()]
                )

    def recommendations(self, text):
        """Shape/provenance guard for advice, never a correctness proof.

        Free-form numerical narrative is never a verified business result.
        """
        lines = re.findall(r"(?m)^\s*(?:\d+[.)]|[-*])\s+(.+)$", text)
        if not lines and re.search(r"recommendations?:", text, re.I):
            lines = re.split(r";", re.split(r"recommendations?:", text, flags=re.I)[-1])
        accepted = []
        for line in lines:
            line = line.strip().strip("*").strip()
            if (
                not line
                or len(line) > 500
                or re.search(
                    r"\d|candidate_|\b(tool|executed|calculated|verified)\b", line, re.I
                )
            ):
                continue
            if line.casefold() not in {item.casefold() for item in accepted}:
                accepted.append(line)
        return accepted[: self.recommendation_count]

    def _arithmetic(self, intent):
        operations = {
            "add": r"\b(add|sum)\b",
            "subtract": r"\bsubtract\b",
            "multiply": r"\b(multiply|times)\b",
            "divide": r"\bdivide\b",
        }
        for operation, pattern in operations.items():
            if re.search(pattern, intent):
                values = re.findall(r"(?<![\w.])[-+]?\d+(?:\.\d+)?", self.goal)
                if len(values) == 2:
                    return {
                        "operation": operation,
                        "a": float(values[0]),
                        "b": float(values[1]),
                    }
        return None

    def pending(self):
        done = {item["label"] for item in self.results}
        return [item for item in self.requirements if item["label"] not in done]

    def allowed(self, registry):
        names = set()
        if (
            any(item["capability"].builtin for item in self.pending())
            or (self.arithmetic and not self.arithmetic_done)
            or (
                not self.requirements
                and re.search(
                    r"\b(calculate|profit|revenue|cost|roi|margin|rate|average|percent|multiply|divide|add|subtract)\b",
                    self.goal,
                    re.I,
                )
            )
        ):
            names.add("calculate")
        if self.csv:
            names.add("analyze_csv")
        if self.trend:
            names.add("analyze_trend")
        if self.docs:
            names.add("search_documents")
        if self.workspace:
            names.update(("list_workspace_files", "read_workspace_file"))
        if self.report:
            names.add("create_report")
        contracts = {item["capability"].contract for item in self.pending()}
        for name, spec in registry.tools.items():
            manifest = getattr(spec, "artifact", {}).get("manifest", {})
            if manifest.get("demo_contract") in contracts:
                names.add(name)
            elif manifest.get("test_provenance") == "model_generated_independent":
                # Approved general tools are offered to explicitly opted-in demo tasks.
                if getattr(self.body, "demo_tools", False):
                    names.add(name)
            elif manifest and not manifest.get("demo_contract") and name in self.goal:
                names.add(name)
        return names & registry.tools.keys()

    def definitions(self, registry):
        definitions = [
            spec.definition()
            for name, spec in registry.tools.items()
            if name in self.allowed(registry)
        ]
        # Requests resolve against the real registry, including built-in adapters.
        for name in dict.fromkeys(item["capability"].name for item in self.pending()):
            c = next(
                item["capability"]
                for item in self.pending()
                if item["capability"].name == name
            )
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": c.description
                        + " Capability request: resolves to an existing tool or reports a real missing capability.",
                        "parameters": c.inputs,
                    },
                }
            )
        return definitions

    def check(self, name, arguments, registry):
        if name in registry.tools and name not in self.allowed(registry):
            raise ToolError(
                "irrelevant_tool",
                f"{name} is irrelevant or lacks authorized data for this goal. Available relevant tools: {', '.join(sorted(self.allowed(registry)))}. CSV requires an actual attached upload; workspace access requires an explicit file task.",
            )
        if (
            name == "read_workspace_file"
            and arguments.get("path") not in self.paths | self.listed_paths
        ):
            raise ToolError(
                "ungrounded_file",
                "File path was not supplied by the user or observed in an actual workspace listing. List the requested workspace first; never invent a filename.",
            )
        if name == "create_report" and (
            self.pending()
            or (self.arithmetic and not self.arithmetic_done)
            or (self.csv and not self.csv_done)
        ):
            raise ToolError(
                "prerequisite_missing",
                "Required calculations/data analysis must execute successfully before writing their report.",
            )
        if name == "calculate" and self.arithmetic and arguments != self.arithmetic:
            # Missing/invalid fields get schema errors first; well-formed changed operands do not run.
            registry.validate(name, arguments)
            raise ToolError(
                "ungrounded_arguments",
                "Calculation operands/operation must match the user request: "
                + json.dumps(self.arithmetic),
            )
        if name == "calculate" and self.requirements:
            registry.validate(name, arguments)
            if not any(
                item["capability"].builtin
                and arguments == item["capability"].builtin_arguments(item["arguments"])
                for item in self.requirements
            ):
                raise ToolError(
                    "ungrounded_arguments",
                    "This calculation does not match a requested business result. Use the pending grounded capability and its exact inputs.",
                )

    def required_tool(self, name):
        if name == "list_workspace_files":
            return self.workspace and bool(re.search(r"\blist\b", self.goal, re.I))
        if name == "read_workspace_file":
            return self.workspace and bool(
                re.search(r"\b(read|open|summarize)\b", self.goal, re.I)
            )
        if name == "create_report":
            return self.report
        if name == "analyze_csv":
            return self.csv
        if name == "analyze_trend":
            return self.trend
        if name == "search_documents":
            return self.docs
        return bool(self.arithmetic or self.requirements)

    def observe(self, name, arguments, result):
        if name == "list_workspace_files":
            self.list_done = True
            self.listed_paths.update(
                item["path"]
                for item in result.get("files", [])
                if item.get("kind") == "file"
            )
        if name == "read_workspace_file":
            self.read_done = True
        if name == "analyze_csv":
            self.csv_done = True
        if name == "analyze_trend":
            self.csv_done = self.trend_done = True
        if name == "search_documents":
            self.docs_done = True
        if name == "create_report":
            self.report_done = True
            self.report_output = result
        if name == "calculate" and self.arithmetic == arguments:
            self.arithmetic_done = True

    def conversational(self):
        """Plain chat (greetings, general questions): nothing to compute, read or attach."""
        return not (
            self.requirements
            or self.missing_operands
            or self.csv
            or self.docs
            or self.workspace
            or self.report
            or self.arithmetic
            or self.paths
            or self.body.upload_ids
            or re.search(r"\d", self.goal)
        )

    def missing(self):
        missing = [item["label"] for item in self.pending()]
        if self.arithmetic and not self.arithmetic_done:
            missing.append("requested arithmetic")
        if self.report and not self.report_done:
            missing.append("actual saved report")
        if self.csv and not self.csv_done:
            missing.append("attached CSV analysis")
        if self.trend and not self.trend_done:
            missing.append("trend and anomaly analysis")
        if self.docs and not self.docs_done:
            missing.append("document search")
        if (
            self.workspace
            and re.search(r"\bread\b", self.goal, re.I)
            and not self.read_done
        ):
            missing.append("requested file read")
        if (
            self.workspace
            and re.search(r"\blist\b", self.goal, re.I)
            and not self.list_done
        ):
            missing.append("requested directory listing")
        return missing

    def next_action(self):
        """Only grounded unmet numeric requirements may provide deterministic recovery."""
        if self.pending():
            item = self.pending()[0]
            return item["capability"].name, item["arguments"]
        if self.arithmetic and not self.arithmetic_done:
            return "calculate", self.arithmetic
        return None

    def simple(self):
        if (
            self.requirements
            and self.requirements[0]["capability"].name == "median_numbers"
        ):
            from ..improvement.demo_contracts import median_only_goal

            return median_only_goal(self.goal)
        # Automatic completion cannot silently drop requested prose/report/extra arithmetic.
        intent = re.sub(r"\{[^{}]*\}", "", self.goal.lower())
        return (
            len(self.requirements) == 1
            and not self.report
            and not self.workspace
            and not self.csv
            and not re.search(
                r"\b(and|recommend|explain|compare|scenario|actions|strategy)\b", intent
            )
            and (
                "{" in self.goal
                or self.requirements[0]["capability"].name == "median_numbers"
            )
        )
