import { Button } from "@/components/ui/button";

/** Suggestions fill the answer; only the explicit send submits it. */
export function QuestionChoices({
  choices,
  choose,
  disabled,
}: {
  choices: string[];
  choose: (answer: string) => void;
  disabled?: boolean;
}) {
  if (!choices.length) return null;
  return (
    <div className="actions" role="group" aria-label="Suggested answers">
      {choices.map((choice) => (
        <Button
          key={choice}
          type="button"
          variant="outline"
          className="h-auto whitespace-normal text-left"
          disabled={disabled}
          onClick={() => choose(choice)}
        >
          {choice}
        </Button>
      ))}
    </div>
  );
}
