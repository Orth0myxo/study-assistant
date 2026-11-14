from pydantic import BaseModel, ConfigDict, Field, StrictInt


class DocumentEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    remove_words: StrictInt = Field(ge=1, le=100)


class DocumentEditResponse(BaseModel):
    job_id: str
    document_id: str
    filename: str
    original_text: str
    edited_text: str
    removed_words: list[str]
    removed_count: int
