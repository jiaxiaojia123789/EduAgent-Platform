from typing import List, Dict, Any
import re


class BenchmarkHarness:
    """
    Agent Harness: Educational QA & Pedagogical Benchmark Evaluator
    Automates regression testing on test suites, calculating:
    - Top-5 Recall Rate
    - Hallucination occurrence rate
    - LaTeX formula syntactic validity
    - Pedagogical accuracy
    """

    @staticmethod
    def validate_latex_syntax(text: str) -> Dict[str, Any]:
        """Detects unclosed LaTeX delimiters $ or $$ in generated content."""
        # Check inline math $...$
        # Remove escaped \$ first
        clean = text.replace(r"\$", "")
        double_dollars = len(re.findall(r"\$\$", clean))
        # Remove double dollars before checking single
        without_double = re.sub(r"\$\$", "", clean)
        single_dollars = len(re.findall(r"\$", without_double))

        is_valid = (double_dollars % 2 == 0) and (single_dollars % 2 == 0)
        return {
            "is_valid": is_valid,
            "double_dollar_count": double_dollars,
            "single_dollar_count": single_dollars,
            "error": None if is_valid else "检测到未闭合的 LaTeX 公式定界符 ($ 或 $$)"
        }

    @staticmethod
    def evaluate_hallucination(answer: str, context_chunks: List[str]) -> float:
        """
        Calculates heuristic grounding score (ratio of key claim tokens supported by retrieved context).
        Lower hallucination score = better alignment.
        """
        if not context_chunks:
            return 0.5  # Neutral default

        combined_context = " ".join(context_chunks)
        # Extract 4-character n-grams from answer
        ngrams = [answer[i:i+4] for i in range(len(answer) - 3)]
        if not ngrams:
            return 0.0

        supported = sum(1 for ng in ngrams if ng in combined_context)
        grounding_ratio = supported / len(ngrams)
        hallucination_rate = max(0.0, min(1.0, 1.0 - grounding_ratio * 1.5))
        return round(hallucination_rate, 4)

    @staticmethod
    def run_benchmark_suite(test_cases: List[Dict[str, Any]], agent_runner_func) -> Dict[str, Any]:
        """Runs batch evaluation over a dataset of educational QA pairs."""
        total = len(test_cases)
        if total == 0:
            return {"total": 0, "status": "empty"}

        valid_latex_count = 0
        hallucination_scores = []

        for case in test_cases:
            query = case["query"]
            result = agent_runner_func(query)
            ans = result.get("output", "")

            latex_check = BenchmarkHarness.validate_latex_syntax(ans)
            if latex_check["is_valid"]:
                valid_latex_count += 1

            retrieved = [doc.get("content", "") for doc in result.get("retrieved_docs", [])]
            h_score = BenchmarkHarness.evaluate_hallucination(ans, retrieved)
            hallucination_scores.append(h_score)

        avg_hallucination = sum(hallucination_scores) / total if total > 0 else 0
        # 注意：召回率与准确率需接入真实知识库 + 标注 QA 对后由评测脚本产出，
        # 此处不再硬编码估计值，避免与真实运行结果口径不一致。
        return {
            "total_evaluated": total,
            "latex_validity_rate": round(valid_latex_count / total * 100, 2),
            "average_hallucination_rate": round(avg_hallucination * 100, 2),
        }
