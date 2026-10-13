/* 담당자 원형 아바타(28px): avatar 바탕 + 이름 첫 글자. 이름은 옆에 글자로 항상 함께 보인다 */
export function Avatar({ name }: { name: string }) {
  const initial = Array.from(name.trim())[0] ?? "?";
  return (
    <span
      aria-hidden="true"
      className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-mn-avatar text-[13px] font-semibold leading-5 text-mn-text"
    >
      {initial}
    </span>
  );
}
